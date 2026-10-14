from decimal import Decimal

import pytest

from app.strategy.indicators import Candle
from app.strategy.registry import (
    StrategyRegistry, StrategyRouter, classify_regime, route_intent,
)


def flat(n, price=100, vol="5"):
    p = Decimal(str(price))
    return tuple(Candle(i, p - Decimal("0.02"), p + Decimal("0.05"),
                        p - Decimal("0.05"), p, Decimal(vol)) for i in range(n))


def trend_candles(n=40, start=100.0, step=0.6):
    out = []
    for i in range(n):
        close = Decimal(str(round(start + step * i, 6)))
        out.append(Candle(i, close - Decimal("0.02"), close + Decimal("0.05"),
                          close - Decimal("0.05"), close, Decimal("5")))
    return tuple(out)


def wild_candles(n=40):
    # Large ranges relative to price -> ATR/close > 5%.
    out = []
    for i in range(n):
        c = Decimal("100")
        out.append(Candle(i, c, c + Decimal("8"), c - Decimal("8"), c, Decimal("5")))
    return tuple(out)


def test_trend_regime_detected_without_lookahead():
    assert classify_regime(trend_candles(40)) == "trend"
    down = trend_candles(40, start=200.0, step=-0.6)
    assert classify_regime(down) == "trend"


def test_range_regime_for_sideways_series():
    assert classify_regime(flat(40)) == "range"


def test_high_volatility_regime_dominates():
    assert classify_regime(wild_candles(40)) == "high_volatility"


def test_classification_is_parmonotonic_and_fail_closed():
    noisy = list(trend_candles(40))
    noisy[20], noisy[21] = noisy[21], noisy[20]
    with pytest.raises(ValueError):
        classify_regime(tuple(noisy))
    with pytest.raises(ValueError, match="at least"):
        classify_regime(trend_candles(15))
    dup = list(trend_candles(40))
    dup.append(dup[-1])  # duplicate timestamp
    with pytest.raises(ValueError):
        classify_regime(tuple(dup))
    with pytest.raises(ValueError, match="trend_spread_fraction"):
        classify_regime(trend_candles(40), trend_spread_fraction=Decimal("0"))


def test_router_defaults_and_stand_aside():
    router = StrategyRouter()
    assert router.strategy_for("trend") == "trend"
    assert router.strategy_for("range") == "breakout"
    assert router.strategy_for("high_volatility") is None
    with pytest.raises(ValueError, match="unknown regimes"):
        StrategyRouter({"trend": "trend", "range": "breakout", "sideways": "x"})
    with pytest.raises(ValueError, match="must cover"):
        StrategyRouter({"trend": "trend"})
    with pytest.raises(ValueError, match="unknown regime"):
        router.strategy_for("sideways")


def test_route_intent_is_explainable_and_fail_closed():
    seen = {}

    def mock_strategy(history, equity):
        seen["len"] = len(history)
        from app.validation.backtest import TradeIntent
        return TradeIntent(history[-1].timestamp, "long", Decimal("1"),
                           Decimal("1"), Decimal("2"))

    registry = StrategyRegistry({"trend": mock_strategy, "breakout": mock_strategy})
    decision, intent = route_intent(trend_candles(40), equity=Decimal("1000"),
                                    registry=registry)
    assert decision.regime == "trend" and decision.strategy_name == "trend"
    assert intent is not None and seen["len"] == 40
    assert "emitted an intent" in decision.reason

    aside_router = StrategyRouter({"trend": None, "range": "breakout",
                                   "high_volatility": None})
    decision, intent = route_intent(trend_candles(40), equity=Decimal("1000"),
                                    registry=registry, router=aside_router)
    assert decision.strategy_name is None and intent is None
    assert "stand-aside" in decision.reason

    with pytest.raises(KeyError, match="unknown strategy"):
        route_intent(flat(40), equity=Decimal("1000"),
                     registry=StrategyRegistry({"trend": mock_strategy}))
