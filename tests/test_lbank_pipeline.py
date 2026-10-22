from app.core.lbank_public import LBankSpotPublicClient
from app.core.lbank_pipeline import fetch_closed_candles, scan_lbank_spot
from app.core.strategy_engine import SignalAction


def test_fetch_closed_candles_excludes_forming_bar_and_sorts():
    payload = {
        "result": "true",
        "data": [
            [900, "10", "12", "9", "11", "5"],
            [0, "8", "10", "7", "9", "4"],
            [1200, "11", "13", "10", "12", "6"],
        ],
    }
    client = LBankSpotPublicClient(transport=lambda url, timeout: payload)
    candles = fetch_closed_candles(client, "BTCUSDT", interval="minute5", limit=3, now=1500)
    assert [c.timestamp for c in candles] == [0, 900]
    assert all(c.is_closed for c in candles)


def test_fetch_closed_candles_rejects_invalid_rows():
    client = LBankSpotPublicClient(transport=lambda url, timeout: {"result": "true", "data": [[1, 2]]})
    import pytest
    from app.core.lbank_public import LBankAPIError
    with pytest.raises(LBankAPIError):
        fetch_closed_candles(client, "BTCUSDT", now=10000)


def test_spot_scan_fails_closed_when_market_data_is_invalid():
    client = LBankSpotPublicClient(transport=lambda url, timeout: {"result": "false", "data": []})
    import pytest
    from app.core.lbank_public import LBankAPIError
    with pytest.raises(LBankAPIError):
        client.get_24h_tickers()
