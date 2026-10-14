import pytest

from app.market_data.router import MarketDataRouter
from app.market_data.types import MarketTypeUnavailableError


class FakeClient:
    def __init__(self, label):
        self.label = label
        self.calls = []

    def fetch_ohlcv(self, symbol, interval, limit):
        self.calls.append((symbol, interval, limit))
        return [(self.label, symbol, interval, limit)]


def test_spot_request_uses_only_spot_adapter():
    spot = FakeClient("spot")
    futures = FakeClient("futures")
    router = MarketDataRouter(spot_client=spot, futures_client=futures)

    result = router.fetch_ohlcv("spot", "btc_usdt", "15m", 50)

    assert result == [("spot", "btc_usdt", "15m", 50)]
    assert spot.calls == [("btc_usdt", "15m", 50)]
    assert futures.calls == []


def test_futures_request_uses_only_futures_adapter():
    spot = FakeClient("spot")
    futures = FakeClient("futures")
    router = MarketDataRouter(spot_client=spot, futures_client=futures)

    result = router.fetch_ohlcv("futures", "btc_usdt", "15m", 25)

    assert result == [("futures", "btc_usdt", "15m", 25)]
    assert spot.calls == []
    assert futures.calls == [("btc_usdt", "15m", 25)]


def test_missing_futures_adapter_fails_closed_without_spot_fallback():
    spot = FakeClient("spot")
    router = MarketDataRouter(spot_client=spot)

    with pytest.raises(MarketTypeUnavailableError, match="no Spot fallback"):
        router.fetch_ohlcv("futures", "btc_usdt", "15m")

    assert spot.calls == []


def test_invalid_market_type_is_rejected_before_network():
    spot = FakeClient("spot")
    router = MarketDataRouter(spot_client=spot)

    with pytest.raises(ValueError, match="market_type"):
        router.fetch_ohlcv("margin", "btc_usdt", "15m")

    assert spot.calls == []


def test_spot_client_is_required():
    with pytest.raises(ValueError, match="spot_client"):
        MarketDataRouter(spot_client=None)
