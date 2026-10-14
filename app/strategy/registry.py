"""Multi-strategy scaffolding: registry, mechanical regime classification, dispatch.

Design rules (documented to keep the evidence chain honest):
- A strategy is any callable ``(history, timestamp, equity) -> TradeIntent | None``;
  it sees ONLY trailing candles (never future data) and may return None
  (warm-up, no setup, fail-closed rejection).
- The regime classifier is mechanical and uses only trailing candles —
  its rules must be validated on train windows before any live selection,
  and selection maps are FROZEN configuration, never fitted on test results
  (otherwise the test set stops being out-of-sample).
- Dispatch failure modes (unknown regime, unknown strategy) raise loudly;
  strategy failures return None — a non-trade is always the safe default.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Mapping

from app.strategy.indicators import Candle, atr, sma, validate_candles
from app.validation.backtest import TradeIntent

StrategyFn = Callable[[tuple[Candle, ...], int, Decimal], "TradeIntent | None"]

REGIMES = ("trend", "range", "high_volatility")

# Thresholds are policy inputs; they must come from train-window calibration,
# never from test-window performance.
REGIME_TREND_SLOPE_ATR_FRACTION = Decimal("0.25")  # slow-SMA move over lookback >= 0.25 x ATR
REGIME_HIGH_VOL_FRACTION = Decimal("0.05")         # ATR/close above the analyzer's 5% line
REGIME_WINDOW = 21                                  # slow-SMA lookback window


class StrategyRegistry:
    """Fail-closed name -> strategy map; duplicate or invalid names are rejected."""

    def __init__(self) -> None:
        self._strategies: dict[str, StrategyFn] = {}

    def register(self, name: str, fn: StrategyFn) -> "StrategyRegistry":
        if not isinstance(name, str) or not name.strip():
            raise ValueError("strategy name must be a non-empty string")
        key = name.strip().lower()
        if not callable(fn):
            raise TypeError("strategy must be callable")
        if key in self._strategies:
            raise ValueError(f"strategy {key!r} is already registered")
        self._strategies[key] = fn
        return self

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._strategies))

    def get(self, name: str) -> StrategyFn:
        key = str(name).strip().lower()
        try:
            return self._strategies[key]
        except KeyError:
            raise ValueError(
                f"unknown strategy {key!r}; registered: {', '.join(self.names) or '(none)'}"
            ) from None


@dataclass(frozen=True, slots=True)
class RegimeRead:
    regime: str
    close: Decimal
    sma_slow: Decimal
    sma_drift: Decimal   # slow SMA now minus N candles ago
    volatility: Decimal  # ATR
    reasons: tuple[str, ...]


def classify_regime(candles: tuple[Candle, ...]) -> RegimeRead:
    """Mechanically classify trailing candles as trend / range / high_volatility.

    Needs at least REGIME_WINDOW + 15 candles (SMA(21) drift + ATR(14)); fewer
    raises ValueError so callers can decide their own warm-up floor.
    """
    data = validate_candles(tuple(candles), min_count=REGIME_WINDOW + 15)
    closes = [c.close for c in data]
    close, volatility = closes[-1], atr(data, 14)
    if close <= 0:
        raise ValueError("close must be greater than zero")
    sma_now = sma(closes, REGIME_WINDOW)
    sma_then = sma(closes[: -REGIME_WINDOW], REGIME_WINDOW) \
        if len(closes) >= 2 * REGIME_WINDOW else sma(closes[: -REGIME_WINDOW], len(closes) - REGIME_WINDOW)
    drift = sma_now - sma_then

    reasons: list[str] = []
    regime = "range"
    if volatility / close > REGIME_HIGH_VOL_FRACTION:
        regime = "high_volatility"
        reasons.append(f"ATR/close {volatility / close:.4f} exceeds {REGIME_HIGH_VOL_FRACTION}")
    elif abs(drift) >= REGIME_TREND_SLOPE_ATR_FRACTION * volatility:
        regime = "trend"
        direction = "up" if drift > 0 else "down"
        reasons.append(f"slow-SMA drift {drift} is a {direction} trend vs ATR threshold")
    else:
        reasons.append("slow-SMA drift is flat relative to ATR")
    return RegimeRead(regime, close, sma_now, drift, volatility, tuple(reasons))


@dataclass(frozen=True, slots=True)
class StrategyRouter:
    """Frozen regime -> strategy-name map over a registry."""
    registry: StrategyRegistry
    mapping: Mapping[str, str]
    fallback: str | None = None  # None = stand aside (no trade) is a strategy too

    def __post_init__(self) -> None:
        names = set(self.registry.names)
        for regime, name in self.mapping.items():
            if regime not in REGIMES:
                raise ValueError(f"unknown regime {regime!r}; use one of {REGIMES}")
            if name.strip().lower() not in names:
                raise ValueError(f"mapping points to unregistered strategy {name!r}")
        if self.fallback is not None and self.fallback.strip().lower() not in names:
            raise ValueError(f"fallback points to unregistered strategy {self.fallback!r}")

    def strategy_for(self, regime: str) -> StrategyFn | None:
        key = str(regime).strip().lower()
        if key not in REGIMES:
            raise ValueError(f"unknown regime {key!r}")
        name = self.mapping.get(key, self.fallback)
        if name is None:
            return None  # stand-aside: no strategy assigned to this regime
        return self.registry.get(name)
