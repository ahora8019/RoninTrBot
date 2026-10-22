import pytest

from app.core.lbank_public import (
    LBankAPIError,
    LBankBookTicker,
    LBankSpotPublicClient,
    LBankTicker24h,
)


def test_list_symbols_normalizes_and_deduplicates():
    client = LBankSpotPublicClient(transport=lambda url, timeout: {
        "result": "true", "data": ["BTC_USDT", "btc_usdt", "eth_usdt"]
    })
    assert client.list_symbols() == ["btc_usdt", "eth_usdt"]


def test_get_tickers_parses_documented_payload():
    client = LBankSpotPublicClient(transport=lambda url, timeout: {
        "result": "true",
        "data": [{
            "symbol": "btc_usdt",
            "ticker": {"high": "110", "low": "90", "vol": "10", "turnover": "1000", "latest": "100"},
            "timestamp": 1_700_000_000_000,
        }],
    })
    result = client.get_24h_tickers()
    assert result == [LBankTicker24h("btc_usdt", 110, 90, 10, 1000, 100, 1_700_000_000_000)]


def test_book_ticker_normalizes_btcusdt_symbol():
    calls = []
    def transport(url, timeout):
        calls.append(url)
        return {"result": "true", "data": {
            "symbol": "btc_usdt", "bidPrice": "99", "askPrice": "101",
            "bidQty": "2", "askQty": "3",
        }}
    book = LBankSpotPublicClient(transport=transport).get_book_ticker("BTCUSDT")
    assert book == LBankBookTicker("btc_usdt", 99, 101, 2, 3)
    assert "symbol=btc_usdt" in calls[0]


def test_to_snapshot_calculates_spread_and_uses_quote_turnover():
    client = LBankSpotPublicClient(clock=lambda: 1_700_000_001)
    ticker = LBankTicker24h("btc_usdt", 110, 90, 10, 500_000, 100, 1_700_000_000_000)
    book = LBankBookTicker("btc_usdt", 99.9, 100.1, 2, 3)
    snapshot = client.to_snapshot(ticker, book)
    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.volume_quote == 500_000
    assert snapshot.spread_bps == pytest.approx(20)
    assert snapshot.data_age_seconds == pytest.approx(1)
    assert snapshot.market_type.value == "spot"


@pytest.mark.parametrize("payload", [
    None,
    [],
    {"result": "false", "error_code": 1001, "msg": "bad request"},
    {"result": "true"},
])
def test_invalid_or_failed_api_response_fails_closed(payload):
    client = LBankSpotPublicClient(transport=lambda url, timeout: payload)
    with pytest.raises(LBankAPIError):
        client.list_symbols()


def test_invalid_order_book_is_rejected():
    client = LBankSpotPublicClient(transport=lambda url, timeout: {
        "result": "true", "data": {
            "symbol": "btc_usdt", "bidPrice": "102", "askPrice": "101",
            "bidQty": "2", "askQty": "3",
        }
    })
    with pytest.raises(LBankAPIError):
        client.get_book_ticker("BTCUSDT")
