"""Deterministic trend-following policy: indicators -> risk-sized TradeIntent.

Pure and offline: no network, no orders. The policy evaluates trailing
candles (it never looks ahead by construction — callers must pass only
history up to the decision candle) and converts a thresholded
``analyze_timeframe`` read into a risk-sized ``TradeIntent`` via
``build_risk_plan``. Ineligible plans and unsupported sides (spot short)
fail closed to ``None``.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.risk.engine import RiskLimits, build_risk_plan
from app.strategy.indicators import (
    SIGNAL_THRESHOLD,
    SUPPORTED_TIMEFRAMES,
    Candle,
    analyze_timeframe,
    validate_candles,
)
from app.validation.backtest import TradeIntent

MIN_WARMUP_CANDLES = 51  # matches analyze_timeframe's requirement

# Execution engines (backtest + paper) reject entries whose notional PLUS
# entry fee exceeds their equity cap, so a policy using the default
# max_notional_fraction=1.0 leaves zero fee headroom and can never fill when
# the notional cap binds. Size with e.g. max_notional_fraction=0.95 to leave
# room for fees, unless the risk budget (stop-based) binds before the cap.


@dataclass(frozen=True, slots=True)
class TrendPolicy:
    """Config for trend_intent(); all fields are explicit policy inputs."""
    timeframe: str = "1h"
    market_type: str = "futures"
    symbol: str = "GENERIC"
    threshold: int = SIGNAL_THRESHOLD
    limits: RiskLimits = RiskLimits()
    leverage: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if self.timeframe not in SUPPORTED_TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {', '.join(SUPPORTED_TIMEFRAMES)}")
        market = self.market_type.strip().lower()
        if market not in {"spot", "futures"}:
            raise ValueError("market_type must be 'spot' or 'futures'")
        object.__setattr__(self, "market_type", market)
        if isinstance(self.threshold, bool) or not isinstance(self.threshold, int) \
                or not 0 <= self.threshold <= 100:
            raise ValueError("threshold must be an integer from 0 to 100")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be non-empty")
        if market == "spot" and self.leverage != 1:
            raise ValueError("spot leverage must be 1")


def trend_intent(
    candles: tuple[Candle, ...],
    *,
    equity: Decimal,
    policy: TrendPolicy = TrendPolicy(),
) -> TradeIntent | None:
    """Return a risk-sized intent for the final candle, or None (fail closed).

    ``candles`` must be the history ending at the decision candle; the intent
    is keyed to that candle's timestamp and (in backtest/paper engines) fills
    at the NEXT candle's open. Returns None during indicator warm-up, on
    neutral/sub-threshold reads, on spot-short (unsupported), and whenever
    the risk engine's hard checks reject the plan.
    """
    data = tuple(candles)
    if len(data) < MIN_WARMUP_CANDLES:
        return None
    validated = validate_candles(data)  # structural defects raise (fail closed)

    analysis = analyze_timeframe(policy.timeframe, validated)
    if analysis.direction == "neutral" or analysis.score < policy.threshold:
        return None
    if policy.market_type == "spot" and analysis.direction == "short":
        return None  # mirrors the risk engine's spot-short rejection
    plan = build_risk_plan(
        equity=equity, entry=analysis.close, atr=analysis.atr,
        direction=analysis.direction, market_type=policy.market_type,
        symbol=policy.symbol, limits=policy.limits,
        leverage=policy.leverage if policy.market_type == "futures" else Decimal("1"),
    )
    if not plan.eligible:
        return None
    return TradeIntent(
        validated[-1].timestamp, plan.direction, plan.quantity,
        abs(plan.entry - plan.stop_loss),
        abs(plan.take_profit - plan.entry),
        market_type=plan.market_type,
    )
