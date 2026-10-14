"""Multi-strategy scaffolding: mechanical regime classifier + frozen router.

Design contract (reviewed before implementation):
- The setup->strategy mapping is EXPLICIT and FROZEN at construction time.
  Strategies are never selected by out-of-sample performance (that would
  contaminate the test data); the router only maps a mechanically observable
  regime to a strategy name.
- "Stand aside" is a first-class outcome: a regime mapped to None means the
  system deliberately takes no position (a form of the emergency-stop/halt
  semantics the risk side already embodies).
- Everything here is pure and offline; strategies remain policy callables
  with the shared contract ``(history, equity) -> TradeIntent | None``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Mapping, Optional

from app.strategy.indicators import Candle, atr, sma, validate_candles
from app.validation.backtest import TradeIntent

StrategyFn = Callable[[tuple[Candle, ...], Decimal], Optional[TradeIntent]]

REGIMES = ("trend", "range", "high_volatility")
HIGH_VOL_ATR_FRACTION = Decimal("0.05")   # mirrors analyze_timeframe's ATR<=5% band
DEFAULT_TREND_SPREAD = Decimal("0.005")   # |SMA9-SMA21| >= 0.5% of close => trend
MIN_REGIME_CANDLES = 22                   # SMA(21) + ATR(14) warm-up


def classify_regime(
    candles: tuple[Candle, ...],
    *,
    trend_spread_fraction: Decimal = DEFAULT_TREND_SPREAD,
) -> str:
    """Classify the trailing history into trend / range / high_volatility.

    Mechanical and deterministic; uses only trailing candles (no lookahead).
    Raises ValueError on structural data defects (fail-closed).
    """
    data = validate_candles(tuple(candles))
    if len(data) < MIN_REGIME_CANDLES:
        raise ValueError(f"regime classification needs at least {MIN_REGIME_CANDLES} candles")
    if trend_spread_fraction <= 0:
        raise ValueError("trend_spread_fraction must be greater than zero")
    closes = [c.close for c in data]
    close = closes[-1]
    volatility = atr(data, 14) / close if close > 0 else Decimal("0")
    if volatility > HIGH_VOL_ATR_FRACTION:
        return "high_volatility"
    fast, slow = sma(closes, 9), sma(closes, 21)
    spread = abs(fast - slow) / close if close > 0 else Decimal("0")
    if spread >= trend_spread_fraction and (
            (fast > slow and close > slow) or (fast < slow and close < slow)):
        return "trend"
    return "range"


@dataclass(frozen=True, slots=True)
class StrategyRegistry:
    """Named strategy callables; names are the router's vocabulary."""
    strategies: Mapping[str, StrategyFn] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.strategies, Mapping) or not self.strategies:
            raise ValueError("registry needs at least one named strategy")
        for name, fn in self.strategies.items():
            if not isinstance(name, str) or not name.strip() or name != name:
                raise ValueError("strategy names must be non-empty strings")
            if not callable(fn):
                raise ValueError(f"strategy {name!r} is not callable")

    def get(self, name: str) -> StrategyFn:
        try:
            return self.strategies[name]
        except KeyError:
            raise KeyError(f"unknown strategy {name!r}") from None


@dataclass(frozen=True, slots=True)
class StrategyRouter:
    """Frozen regime -> strategy-name map; None means stand aside."""
    regime_map: Mapping[str, Optional[str]] = field(default_factory=lambda: {
        "trend": "trend", "range": "breakout", "high_volatility": None})

    def __post_init__(self) -> None:
        unknown = set(self.regime_map) - set(REGIMES)
        if unknown:
            raise ValueError(f"regime_map uses unknown regimes: {sorted(unknown)}")
        missing = set(REGIMES) - set(self.regime_map)
        if missing:
            raise ValueError(f"regime_map must cover every regime; missing: {sorted(missing)}")

    def strategy_for(self, regime: str) -> Optional[str]:
        if regime not in REGIMES:
            raise ValueError(f"unknown regime {regime!r}")
        return self.regime_map[regime]


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Fully explainable routing audit record."""
    regime: str
    strategy_name: Optional[str]
    reason: str


def route_intent(
    candles: tuple[Candle, ...],
    *,
    equity: Decimal,
    registry: StrategyRegistry,
    router: StrategyRouter = StrategyRouter(),
    trend_spread_fraction: Decimal = DEFAULT_TREND_SPREAD,
) -> tuple[RoutingDecision, Optional[TradeIntent]]:
    """Classify the regime and ask the mapped strategy for an intent.

    A mapped strategy must exist in the registry (fail-closed otherwise);
    a None mapping means stand aside and is explained in the decision.
    """
    regime = classify_regime(candles, trend_spread_fraction=trend_spread_fraction)
    name = router.strategy_for(regime)
    if name is None:
        return RoutingDecision(regime, None, "stand-aside regime: no position is taken"), None
    intent = registry.get(name)(candles, equity)
    reason = (f"strategy {name!r} emitted an intent" if intent is not None
              else f"strategy {name!r} found no setup")
    return RoutingDecision(regime, name, reason), intent
