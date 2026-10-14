from decimal import Decimal

import pytest

from app.strategy.indicators import Candle
from app.strategy.registry import (
    REGIMES,
    StrategyRegistry,
    StrategyRouter,
    classify_regime,
)
from app.validation.backtest import TradeIntent


def dummy_strategy(name_hit: list):
    def fn(history, timestamp, equity):
        name_hit.append(timestamp)
        return None
    return fn


def trend_candles(n=60, step="0.5", start="100"):
    out = []
    from decimal import Decimal as D
    for i in range(n):
        close = D(start) + D(step) * i
        out.append(Candle(i, close - D("0.02"), close + D("0.05"),
                          close - D("0.05"), close, D("5")))
    return tuple(out)


def range_candles(n=60):
    out = []
    for i in range(n):
        close = Decimal("100") + (Decimal("0.3") if i % 2 else Decimal("-0.3"))
        out.append(Candle(i, close - Decimal("0.02"), close + Decimal("0.05"),
                          close - Decimal("0.05"), close, Decimal("5")))
    return tuple(out)


def vol_candles(n=60):
    # Massive per-candle range: ATR/close > 5%.
    out = []
    for i in range(n):
        close = Decimal("100")
        out.append(Candle(i, close, Decimal("112"), Decimal("92"), close, Decimal("5")))
    return tuple(out)


def test_registry_rejects_duplicates_and_unknown_names():
    registry = StrategyRegistry()
    registry.register("trend", lambda h, ts, eq: None)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("TREND", lambda h, ts, eq: None)
    with pytest.raises(ValueError, match="unknown strategy"):
        registry.get("missing")
    assert registry.names == ("trend",)


def test_registry_dispatch_invokes_registered_callable():
    registry = StrategyRegistry()
    hits = []
    registry.register("trend", dummy_strategy(hits))
    fn = registry.get("trend")
    assert fn(("history",), 7, Decimal("1")) is None
    assert hits == [7]


def test_classify_regime_detects_trend_range_and_high_vol():
    up = classify_regime(trend_candles(step="0.6"))
    assert up.regime == "trend"
    assert up.sma_drift > 0

    sideways = classify_regime(range_candles())
    assert sideways.regime == "range"

    wild = classify_regime(vol_candles())
    assert wild.regime == "high_volatility"


def test_classify_regime_requires_warmup_and_ordered_candles():
    with pytest.raises(ValueError, match="at least"):
        classify_regime(trend_candles(n=20))
    with pytest.raises(ValueError, match="strictly increasing"):
        classify_regime(trend_candles(n=40) + trend_candles(n=40)[-1:])  # duplicate ts


def test_router_maps_regimes_and_rejects_bad_configuration():
    registry = StrategyRegistry()
    registry.register("trend", dummy_strategy([]))
    registry.register("breakout", dummy_strategy([]))

    router = StrategyRouter(registry, {"trend": "trend", "range": None} if False else
                            {"trend": "trend"}, fallback=None)
    assert router.strategy_for("trend") is registry.get("trend")
    assert router.strategy_for("range") is None      # stand-aside by design
    assert router.strategy_for("high_volatility") is None

    with pytest.raises(ValueError, match="unknown regime"):
        router.strategy_for("euphoria")
    with pytest.raises(ValueError, match="unregistered"):
        StrategyRouter(registry, {"trend": "ghost"})
    with pytest.raises(ValueError, match="unknown regime"):
        StrategyRouter(registry, {"sideways": "trend"})


def test_router_returns_intents_from_selected_strategy():
    registry = StrategyRegistry()

    def breakout_like(history, timestamp, equity):
        return TradeIntent(timestamp, "long", Decimal("1"), Decimal("1"), Decimal("2"))

    registry.register("breakout", breakout_like)
    router = StrategyRouter(registry, {"range": "breakout"}, fallback=None)
    fn = router.strategy_for("range")
    intent = fn((), 42, Decimal("1000"))
    assert intent is not None and intent.signal_timestamp == 42
    assert set(REGIMES) == {"trend", "range", "high_volatility"}
