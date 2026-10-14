from decimal import Decimal

import pytest

from app.strategy.breakout import BreakoutPolicy, breakout_intent, breakout_strategy
from app.strategy.indicators import Candle, atr
from app.validation.backtest import TradeIntent


def flat(n, price=100, vol="5"):
    p = Decimal(str(price))
    return tuple(Candle(i, p - Decimal("0.02"), p + Decimal("0.05"),
                        p - Decimal("0.05"), p, Decimal(vol)) for i in range(n))


def with_signal(base: tuple, *, close, high, low, open_, volume, ts=None) -> tuple:
    ts = base[-1].timestamp + 1 if ts is None else ts
    return base + (Candle(ts, Decimal(str(open_)), Decimal(str(high)),
                          Decimal(str(low)), Decimal(str(close)), Decimal(str(volume))),)


def test_warmup_returns_none_without_raising():
    assert breakout_intent(flat(20), equity=Decimal("1000")) is None


def test_long_breakout_with_volume_confirmation_and_exact_geometry():
    base = flat(25)
    candles = with_signal(base, close="101", high="101.1", low="100.4", open_="100.6",
                          volume="20")  # 20 >= 1.5 * 5
    intent = breakout_intent(candles, equity=Decimal("1000"),
                             policy=BreakoutPolicy(market_type="futures"))
    assert isinstance(intent, TradeIntent)
    assert intent.direction == "long"
    assert intent.signal_timestamp == candles[-1].timestamp
    expected_stop = atr(candles, 14) * Decimal("2")
    # Round trip through plan.entry +/- stop at 28-digit precision shifts the
    # tail; geometry itself is exact, so compare within 1e-20.
    assert abs(intent.stop_distance - expected_stop) < Decimal("1E-20")
    assert abs(intent.target_distance - expected_stop * Decimal("2")) < Decimal("1E-20")


def test_price_break_without_volume_confirmation_emits_nothing():
    candles = with_signal(flat(25), close="101", high="101.1", low="100.4",
                          open_="100.6", volume="5")  # 5 < 7.5 needed
    assert breakout_intent(candles, equity=Decimal("1000"),
                           policy=BreakoutPolicy()) is None


def test_volume_spike_without_price_break_emits_nothing():
    candles = with_signal(flat(25), close="100", high="100.05", low="99.95",
                          open_="99.98", volume="50")
    assert breakout_intent(candles, equity=Decimal("1000"),
                           policy=BreakoutPolicy()) is None


def test_short_breakdown_on_futures_with_exact_direction():
    candles = with_signal(flat(25), close="99", high="99.6", low="98.9",
                          open_="99.4", volume="20")
    intent = breakout_intent(candles, equity=Decimal("1000"),
                             policy=BreakoutPolicy(market_type="futures"))
    assert intent.direction == "short"
    assert abs(intent.stop_distance - atr(candles, 14) * Decimal("2")) < Decimal("1E-20")


def test_spot_short_breakdown_fails_closed():
    candles = with_signal(flat(25), close="99", high="99.6", low="98.9",
                          open_="99.4", volume="20")
    assert breakout_intent(candles, equity=Decimal("1000"),
                           policy=BreakoutPolicy(market_type="spot")) is None
    # ...while a long break still works on spot:
    up = with_signal(flat(25), close="101", high="101.1", low="100.4",
                     open_="100.6", volume="20")
    assert breakout_intent(up, equity=Decimal("1000"),
                           policy=BreakoutPolicy(market_type="spot")) is not None


def test_channel_excludes_signal_candle_no_lookahead():
    # A candle equal TO the prior high (not above) must NOT trigger.
    candles = with_signal(flat(25), close="100.05", high="100.05", low="99.9",
                          open_="99.98", volume="20")
    assert breakout_intent(candles, equity=Decimal("1000"),
                           policy=BreakoutPolicy()) is None


def test_structural_defects_raise():
    dup = flat(25) + (flat(1)[0],)
    with pytest.raises(ValueError):
        breakout_intent(dup, equity=Decimal("1000"))


def test_policy_validation_and_registry_adapter():
    with pytest.raises(ValueError):
        BreakoutPolicy(lookback=1)
    with pytest.raises(ValueError):
        BreakoutPolicy(volume_multiplier=Decimal("0"))
    with pytest.raises(ValueError):
        BreakoutPolicy(market_type="margin")
    candles = with_signal(flat(25), close="101", high="101.1", low="100.4",
                          open_="100.6", volume="20")
    assert breakout_strategy(candles, Decimal("1000")) is not None
    assert breakout_strategy(candles, Decimal("1000")) == \
        breakout_intent(candles, equity=Decimal("1000"))
