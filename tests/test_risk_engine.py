from decimal import Decimal

import pytest

from app.risk.engine import RiskLimits, build_risk_plan


def test_long_spot_plan_uses_atr_stop_and_risk_budget():
    plan = build_risk_plan(
        equity="1000", entry="100", atr="2", direction="long",
        market_type="spot", symbol="btc_usdt",
    )
    assert plan.eligible
    assert plan.stop_loss == Decimal("96")
    assert plan.take_profit == Decimal("108")
    assert plan.quantity == Decimal("2.500000")
    assert plan.risk_amount == Decimal("10.000000")
    assert plan.risk_fraction == Decimal("0.01")
    assert plan.symbol == "BTC_USDT"
    assert any("no order" in reason for reason in plan.reasons)


def test_short_futures_plan_is_theoretical_and_separate():
    plan = build_risk_plan(
        equity=1000, entry=100, atr=2, direction="short",
        market_type="futures", symbol="eth_usdt", leverage=2,
        limits=RiskLimits(max_leverage=Decimal("3")),
    )
    assert plan.market_type == "futures"
    assert plan.stop_loss == Decimal("104")
    assert plan.take_profit == Decimal("92")
    assert plan.eligible


def test_spot_short_is_rejected():
    with pytest.raises(ValueError, match="spot short"):
        build_risk_plan(equity=1000, entry=100, atr=2, direction="short",
                        market_type="spot", symbol="BTC_USDT")


@pytest.mark.parametrize("kwargs", [
    {"equity": 0, "entry": 100, "atr": 2, "direction": "long", "market_type": "spot"},
    {"equity": 1000, "entry": 0, "atr": 2, "direction": "long", "market_type": "spot"},
    {"equity": 1000, "entry": 100, "atr": 0, "direction": "long", "market_type": "spot"},
    {"equity": 1000, "entry": 100, "atr": 2, "direction": "hold", "market_type": "spot"},
    {"equity": 1000, "entry": 100, "atr": 2, "direction": "long", "market_type": "margin"},
])
def test_invalid_inputs_fail_closed(kwargs):
    with pytest.raises(ValueError):
        build_risk_plan(symbol="BTC_USDT", **kwargs)


def test_atr_stop_cannot_consume_entry_price():
    with pytest.raises(ValueError, match="smaller than entry"):
        build_risk_plan(equity=1000, entry=10, atr=6, direction="long",
                        market_type="spot", symbol="BTC_USDT")


def test_hard_leverage_cap_is_enforced():
    with pytest.raises(ValueError, match="leverage"):
        build_risk_plan(equity=1000, entry=100, atr=2, direction="long",
                        market_type="futures", symbol="BTC_USDT", leverage=4)


def test_risk_limit_caps_cannot_be_relaxed():
    with pytest.raises(ValueError, match="2%"):
        RiskLimits(max_risk_fraction=Decimal("0.03"))


def test_position_size_is_capped_by_notional_limit():
    limits = RiskLimits(risk_fraction=Decimal("0.02"), max_risk_fraction=Decimal("0.02"),
                        max_notional_fraction=Decimal("0.1"), max_leverage=Decimal("1"))
    plan = build_risk_plan(equity=100, entry=100, atr=1, direction="long",
                           market_type="spot", symbol="BTC_USDT", limits=limits)
    assert plan.notional <= Decimal("10")
    assert plan.quantity == Decimal("0.100000")


def test_short_target_must_remain_positive():
    with pytest.raises(ValueError, match="take-profit target"):
        build_risk_plan(equity=1000, entry=100, atr=30, direction="short",
                        market_type="futures", symbol="BTC_USDT")
