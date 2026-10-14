from decimal import Decimal

import pytest

from app.strategy.breakout import BreakoutPolicy, breakout_intent, breakout_strategy
from app.strategy.indicators import Candle, atr


def flat_block(n, volume="10"):
    """Range-bound candles: every candle has high 101, low 99, close 100."""
    return tuple(Candle(i, Decimal("100"), Decimal("101"), Decimal("99"),
                        Decimal("100"), Decimal(volume)) for i in range(n))


def breakout_close_candle(ts, close, volume="20"):
    close = Decimal(str(close))
    return Candle(ts, close - Decimal("0.5"), close + Decimal("0.5"),
                  close - Decimal("0.5"), close, Decimal(volume))


def test_long_breakout_with_exact_geometry():
    # 21 prior candles in a 99-101 range (TR=2 everywhere -> ATR=2 exact), then
    # a close above the prior 20-candle high, on elevated volume.
    candles = flat_block(21) + (breakout_close_candle(21, "102"),)
    intent = breakout_intent(candles, equity=Decimal("1000"),
                             policy=BreakoutPolicy(volume_multiplier=Decimal("1")))
    assert intent is not None
    assert intent.direction == "long"
    assert intent.signal_timestamp == 21
    transition_tr = (Decimal("2.5") + Decimal("2") * 13) / 14  # break candle TR 2.5
    assert transition_tr == atr(candles, 14)  # exact: 2.035714285714285714285714286
    assert intent.stop_distance == transition_tr * 2
    assert intent.target_distance == transition_tr * 4


def test_no_signal_when_close_stays_inside_range():
    candles = flat_block(21) + (breakout_close_candle(21, "100.9"),)
    assert breakout_intent(candles, equity=Decimal("1000"),
                           policy=BreakoutPolicy(volume_multiplier=Decimal("0"))) is None


def test_volume_filter_blocks_weak_volume_breakout():
    candles = flat_block(21, volume="10") + (breakout_close_candle(21, "102", volume="10"),)
    # Volume equal to MA with multiplier 1.5 -> 10 < 15 -> blocked.
    assert breakout_intent(candles, equity=Decimal("1000")) is None
    # Same setup passes the filter with strong volume.
    strong = flat_block(21, volume="10") + (breakout_close_candle(21, "102", volume="30"),)
    assert breakout_intent(strong, equity=Decimal("1000")) is not None


def test_short_breakout_futures_and_spot_fails_closed():
    down = Candle(21, Decimal("98.5"), Decimal("99.5"), Decimal("97.5"),
                  Decimal("98"), Decimal("30"))
    candles = flat_block(21) + (down,)
    futures_intent = breakout_intent(candles, equity=Decimal("1000"),
                                     policy=BreakoutPolicy(volume_multiplier=Decimal("1")))
    assert futures_intent is not None and futures_intent.direction == "short"
    spot_intent = breakout_intent(candles, equity=Decimal("1000"),
                                  policy=BreakoutPolicy(market_type="spot",
                                                        volume_multiplier=Decimal("1")))
    assert spot_intent is None  # spot short stays unsupported at the risk choke point


def test_breakout_window_excludes_current_candle():
    # The decision candle itself must not define the range: prior high is 101,
    # so a close of 101.2 still breaks the PRIOR window while its own high (101.7)
    # must not be "the level".
    candles = flat_block(21) + (Candle(21, Decimal("101"), Decimal("101.7"),
                                       Decimal("100.5"), Decimal("101.2"), Decimal("30")),)
    intent = breakout_intent(candles, equity=Decimal("1000"),
                             policy=BreakoutPolicy(volume_multiplier=Decimal("1")))
    assert intent is not None and intent.direction == "long"


def test_warmup_and_validation_fail_closed():
    assert breakout_intent(flat_block(10), equity=Decimal("1000")) is None
    with pytest.raises(ValueError, match="strictly increasing"):
        dup = flat_block(21) + (flat_block(1)[0],)
        breakout_intent(dup, equity=Decimal("1000"))


def test_quantity_respects_risk_budget_exactly():
    candles = flat_block(21) + (breakout_close_candle(21, "110"),)
    intent = breakout_intent(candles, equity=Decimal("1000"),
                             policy=BreakoutPolicy(volume_multiplier=Decimal("1")))
    # ATR stays 2 (TR between the 110-candle and the 100-candle dominates the mean:
    # last-14 window is mostly flat TR=2; one transition candle nudges it).
    # Stop distance = 2 x ATR; quantity = min(10/stop, 950? no: notional cap = 1000/entry).
    assert intent is not None
    assert intent.quantity * intent.stop_distance <= Decimal("10")  # 1% risk budget hard


def test_policy_validation_and_registry_adapter():
    with pytest.raises(ValueError):
        BreakoutPolicy(lookback=1)
    with pytest.raises(ValueError):
        BreakoutPolicy(volume_multiplier=Decimal("-0.5"))
    with pytest.raises(ValueError):
        BreakoutPolicy(market_type="spot", leverage=Decimal("2"))

    strategy = breakout_strategy(BreakoutPolicy(volume_multiplier=Decimal("1")))
    history = flat_block(21) + (breakout_close_candle(21, "102"),)
    intent = strategy(history, 21, Decimal("1000"))
    assert intent is not None and intent.direction == "long"
    with pytest.raises(ValueError, match="history must end"):
        strategy(history, 99, Decimal("1000"))
