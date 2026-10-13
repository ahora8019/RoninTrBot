from decimal import Decimal

import pytest

from app.market_data.lbank import (
    Candle,
    LBankMarketDataClient,
    MarketDataAPIError,
    MarketDataError,
    MarketDataHTTPError,
    MarketDataValidationError,
)


NOW = 1_800_000_000


def row(ts=NOW - 900, open_="100", high="110", low="90", close="105", volume="12.5"):
    return [ts, open_, high, low, close, volume]


def client_with(payload, **kwargs):
    calls = []

    def transport(url, params, timeout):
        calls.append((url, dict(params), timeout))
        return payload

    client = LBankMarketDataClient(
        transport=transport,
        clock=lambda: NOW,
        min_request_interval_seconds=0,
        max_retries=kwargs.pop("max_retries", 0),
        **kwargs,
    )
    return client, calls


def test_fetch_normalizes_and_sorts_candles_and_uses_documented_params():
    payload = {
        "result": "true",
        "error_code": 0,
        "data": [row(NOW - 900), row(NOW - 1800, "80", "100", "75", "95", "9")],
    }
    client, calls = client_with(payload)

    candles = client.fetch_ohlcv("BTC_USDT", "15m", 2)

    assert [c.timestamp for c in candles] == [NOW - 1800, NOW - 900]
    assert isinstance(candles[0], Candle)
    assert candles[0].open == Decimal("80")
    assert calls[0][0].endswith("/v2/kline.do")
    # LBank treats `time` as a START timestamp and caps `size` from the start,
    # so the client must request a start window of limit*interval seconds back
    # and `limit + 1` rows to obtain the `limit` most recent candles.
    assert calls[0][1] == {
        "symbol": "btc_usdt",
        "type": "minute15",
        "size": "3",
        "time": str(NOW - 2 * 900),
    }


def test_fetch_history_returns_the_most_recent_limit_candles():
    # The server answers the whole window (limit + 1 rows including the extra
    # boundary candle); the adapter must trim to the most recent `limit`.
    window = [row(NOW - 900 * k) for k in range(3, 0, -1)]
    payload = {"result": "true", "error_code": 0, "data": window}
    client, _ = client_with(payload)

    candles = client.fetch_ohlcv("btc_usdt", "15m", 2)

    assert [c.timestamp for c in candles] == [NOW - 1800, NOW - 900]


def test_rejects_unaligned_candle_timestamp():
    payload = {"result": "true", "error_code": 0, "data": [row(NOW - 870)]}
    client, _ = client_with(payload)
    with pytest.raises(MarketDataValidationError, match="not aligned"):
        client.fetch_ohlcv("btc_usdt", "15m")


def test_rejects_gap_in_candle_series():
    payload = {"result": "true", "error_code": 0, "data": [row(NOW - 2700), row(NOW - 900)]}
    client, _ = client_with(payload)
    with pytest.raises(MarketDataValidationError, match="gap"):
        client.fetch_ohlcv("btc_usdt", "15m")


def test_limit_above_window_safe_maximum_is_rejected():
    client, calls = client_with({"result": "true", "error_code": 0, "data": [row()]})
    with pytest.raises(ValueError, match="limit"):
        client.fetch_ohlcv("btc_usdt", "15m", 2000)
    assert calls == []


def test_oversized_response_is_rejected(monkeypatch):
    import app.market_data.lbank as lbank_module

    class HugeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            return b"x" * size if size > 0 else b"x" * 32

    monkeypatch.setattr(lbank_module, "urlopen", lambda request, timeout: HugeResponse())
    with pytest.raises(MarketDataError, match="maximum allowed size"):
        lbank_module._default_transport("https://example.invalid", {}, 1.0)


@pytest.mark.parametrize(("interval", "api_type"), [("15m", "minute15"), ("1h", "hour1"), ("4h", "hour4")])
def test_supported_intervals(interval, api_type):
    payload = {"result": "true", "error_code": 0, "data": [row(NOW)]}  # NOW is aligned to 15m/1h/4h
    client, calls = client_with(payload)

    client.fetch_ohlcv("eth_usdt", interval, 1)

    assert calls[0][1]["type"] == api_type


@pytest.mark.parametrize("symbol", ["", "BTCUSDT", "btc/usdt", "btc usdt", "../btc_usdt"])
def test_rejects_invalid_symbols_before_network(symbol):
    client, calls = client_with({"result": "true", "error_code": 0, "data": [row()]})

    with pytest.raises(ValueError, match="symbol must use"):
        client.fetch_ohlcv(symbol, "15m")

    assert calls == []


def test_rejects_unsupported_interval_and_invalid_limit():
    client, calls = client_with({"result": "true", "error_code": 0, "data": [row()]})

    with pytest.raises(ValueError, match="Unsupported interval"):
        client.fetch_ohlcv("btc_usdt", "5m")
    with pytest.raises(ValueError, match="limit"):
        client.fetch_ohlcv("btc_usdt", "15m", 2001)
    with pytest.raises(ValueError, match="limit"):
        client.fetch_ohlcv("btc_usdt", "15m", True)
    assert calls == []


def test_rejects_api_error_and_missing_data():
    client, _ = client_with({"result": "false", "error_code": 10001, "data": []})
    with pytest.raises(MarketDataAPIError):
        client.fetch_ohlcv("btc_usdt", "15m")
    client, _ = client_with({"result": "true", "error_code": 0})
    with pytest.raises(MarketDataAPIError, match="missing"):
        client.fetch_ohlcv("btc_usdt", "15m")


@pytest.mark.parametrize(
    "bad_row",
    [
        [NOW - 900, 100, 110, 90, 105],
        [NOW - 900, 100, 95, 90, 105, 1],
        [NOW - 900, 100, 110, 106, 105, 1],
        [NOW - 900, 0, 110, 90, 105, 1],
        [NOW - 900, 100, 110, 90, 105, -1],
        [NOW - 900, 100, float("nan"), 90, 105, 1],
        [NOW - 900.5, 100, 110, 90, 105, 1],
    ],
)
def test_rejects_invalid_candles(bad_row):
    client, _ = client_with({"result": "true", "error_code": 0, "data": [bad_row]})
    with pytest.raises(MarketDataValidationError):
        client.fetch_ohlcv("btc_usdt", "15m")


def test_rejects_duplicate_timestamps():
    client, _ = client_with({"result": "true", "error_code": 0, "data": [row(), row()]})
    with pytest.raises(MarketDataValidationError, match="duplicate"):
        client.fetch_ohlcv("btc_usdt", "15m")


def test_rejects_stale_latest_candle():
    client, _ = client_with({"result": "true", "error_code": 0, "data": [row(NOW - 4 * 900)]})
    with pytest.raises(MarketDataValidationError, match="stale"):
        client.fetch_ohlcv("btc_usdt", "15m")


def test_retries_transient_http_error_then_succeeds():
    calls = []
    payload = {"result": "true", "error_code": 0, "data": [row()]}

    def transport(url, params, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise MarketDataHTTPError(503)
        return payload

    sleeps = []
    client = LBankMarketDataClient(
        transport=transport,
        clock=lambda: NOW,
        min_request_interval_seconds=0,
        max_retries=1,
        sleep=sleeps.append,
    )

    assert len(client.fetch_ohlcv("btc_usdt", "15m", 1)) == 1
    assert len(calls) == 2
    assert sleeps == [0.25]


def test_does_not_retry_non_transient_http_error():
    calls = []

    def transport(url, params, timeout):
        calls.append(1)
        raise MarketDataHTTPError(404)

    client = LBankMarketDataClient(
        transport=transport,
        clock=lambda: NOW,
        min_request_interval_seconds=0,
        max_retries=2,
        sleep=lambda _: None,
    )
    with pytest.raises(MarketDataHTTPError):
        client.fetch_ohlcv("btc_usdt", "15m", 1)
    assert len(calls) == 1


def test_empty_candle_response_is_rejected():
    client, _ = client_with({"result": "true", "error_code": 0, "data": []})
    with pytest.raises(MarketDataValidationError, match="no candle"):
        client.fetch_ohlcv("btc_usdt", "15m")
