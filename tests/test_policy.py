from decimal import Decimal

import pytest

from app.strategy.indicators import Candle, atr
from app.strategy.policy import TrendPolicy, trend_intent
from app.validation.backtest import TradeIntent


def drift_candles(n, start=100.0, step=0.4):
    """Low-volatility monotonic drift: TR is exactly |step| + 0.05 per candle."""
    out = []
    for i in range(n):
        close = Decimal(str(round(start + step * i, 6)))
        open_ = close - Decimal("0.02")  # inside [low, high]
        out.append(Candle(i, open_, close + Decimal("0.05"),
                          close - Decimal("0.05"), close, Decimal("5")))
    return tuple(out)


def test_intent_is_none_during_warmup():
    candles = drift_candles(50)
    assert trend_intent(candles, equity=Decimal("1000")) is None


def test_long_intent_on_uptrend_with_exact_risk_geometry():
    candles = drift_candles(60, step=0.4)
    intent = trend_intent(candles, equity=Decimal("1000"))
    assert intent is not None
    assert intent.direction == "long"
    assert intent.signal_timestamp == candles[-1].timestamp
    # TR = 0.45 everywhere (see drift_candles) -> stop = 2*0.45, target = 2x stop.
    assert atr(candles, 14) == Decimal("0.45")
    assert intent.stop_distance == Decimal("0.9")
    assert intent.target_distance == Decimal("1.8")


def test_short_intent_on_downtrend_futures():
    candles = tuple(
        Candle(i, c + Decimal("0.2"), c + Decimal("0.05") + Decimal("0.25"),
               c - Decimal("0.25"), c, Decimal("5"))
        for i, c in enumerate(Decimal("120") - Decimal("0.4") * i for i in range(60))
    )
    intent = trend_intent(candles, equity=Decimal("1000"),
                          policy=TrendPolicy(market_type="futures"))
    assert intent is not None
    assert intent.direction == "short"
    assert intent.market_type == "futures"


def test_spot_short_fails_closed_to_none_not_exception():
    down = drift_candles(60, start=200.0, step=-0.4)
    assert trend_intent(down, equity=Decimal("1000"),
                        policy=TrendPolicy(market_type="spot")) is None


def test_subthreshold_or_neutral_market_emits_nothing():
    # Sideways zig-zag: mixed trend -> neutral direction.
    candles = tuple(
        Candle(i, Decimal("100"), Decimal("100.3"), Decimal("99.7"),
               Decimal("100") + Decimal("0.2") * (1 if i % 2 else -1), Decimal("5"))
        for i in range(60)
    )
    assert trend_intent(candles, equity=Decimal("1000")) is None


def test_quantity_respects_risk_budget_and_hard_caps_exactly():
    # Case 1: notional binds (stop 0.9 < 1% of entry 123.6).
    intent = trend_intent(drift_candles(60, step=0.4), equity=Decimal("1000"))
    assert intent.quantity == Decimal("8.090614")  # floor(1000/123.6 / 1e-6) * 1e-6
    # Case 2: risk budget binds (stop 4.1 > 1% of entry 218).
    intent2 = trend_intent(drift_candles(60, step=2.0), equity=Decimal("1000"))
    assert intent2.stop_distance == Decimal("4.1")
    assert intent2.quantity == Decimal("2.439024")  # floor(10/4.1 / 1e-6) * 1e-6
    risk_amount = intent2.quantity * intent2.stop_distance
    assert risk_amount <= Decimal("10")  # 1% risk budget never exceeded


def test_ineligible_plan_returns_none():
    # max_risk_fraction below what the stop distance implies -> plan ineligible.
    from app.risk.engine import RiskLimits
    limits = RiskLimits(risk_fraction=Decimal("0.02"), atr_stop_multiple=Decimal("2"))
    # equity tiny relative to step sizing -> quantity step rounds to zero -> error? No:
    # build_risk_plan raises only when quantity <= 0 after rounding; choose equity
    # that yields a positive but ineligible-vs-hard-cap plan via tiny notional cap.
    limits = RiskLimits(max_notional_fraction=Decimal("0.000001"))
    candles = drift_candles(60, step=0.4)
    with pytest.raises(ValueError):
        trend_intent(candles, equity=Decimal("1"), policy=TrendPolicy(limits=limits))


def test_duplicate_timestamps_fail_closed_with_exception_not_silent_pass():
    bad = drift_candles(60) + (drift_candles(1)[0],)
    with pytest.raises(ValueError):
        trend_intent(bad, equity=Decimal("1000"))


def test_policy_config_validation():
    with pytest.raises(ValueError):
        TrendPolicy(timeframe="2h")
    with pytest.raises(ValueError):
        TrendPolicy(market_type="margin")
    with pytest.raises(ValueError):
        TrendPolicy(market_type="spot", leverage=Decimal("2"))
