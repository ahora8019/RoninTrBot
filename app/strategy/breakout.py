"""Breakout strategy: Donchian-style N-candle range break with a volume filter.

Decision at the final candle (its OHLCV is known at decision time; fills in
backtest/paper engines happen at the NEXT open):
- long  when close exceeds the highest high of the PRIOR `lookback` candles,
- short when close breaks the lowest low of the PRIOR `lookback` candles
  (futures only — spot short stays unsupported, mirroring the risk engine),
- only if current volume >= volume MA(lookback) * volume_multiplier.

Sizing and stop/target geometry reuse ``build_risk_plan`` (ATR stops, risk
budget, hard caps), so this strategy passes the exact same risk choke point
as the trend policy. Warm-up, weak volume, no-break markets, and ineligible
plans all fail closed to None. Pure and offline; never looks ahead.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.risk.engine import RiskLimits, build_risk_plan
from app.strategy.indicators import Candle, atr, validate_candles
from app.validation.backtest import TradeIntent


@dataclass(frozen=True, slots=True)
class BreakoutPolicy:
    lookback: int = 20
    volume_multiplier: Decimal = Decimal("1.5")
    market_type: str = "futures"
    symbol: str = "GENERIC"
    limits: RiskLimits = RiskLimits()
    leverage: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if isinstance(self.lookback, bool) or not isinstance(self.lookback, int) \
                or self.lookback < 2:
            raise ValueError("lookback must be an integer >= 2")
        mult = self.volume_multiplier
        if not isinstance(mult, Decimal):
            mult = Decimal(str(mult))
        object.__setattr__(self, "volume_multiplier", mult)
        if not mult.is_finite() or mult < 0:
            raise ValueError("volume_multiplier must be a finite decimal >= 0")
        market = self.market_type.strip().lower()
        if market not in {"spot", "futures"}:
            raise ValueError("market_type must be 'spot' or 'futures'")
        object.__setattr__(self, "market_type", market)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be non-empty")
        if market == "spot" and self.leverage != 1:
            raise ValueError("spot leverage must be 1")

    @property
    def min_candles(self) -> int:
        return max(self.lookback + 1, 15)  # ATR(14) warm-up floor


def breakout_intent(
    candles: tuple[Candle, ...],
    *,
    equity: Decimal,
    policy: BreakoutPolicy = BreakoutPolicy(),
) -> TradeIntent | None:
    """Return a risk-sized breakout intent for the final candle, or None."""
    data = tuple(candles)
    if len(data) < policy.min_candles:
        return None
    validated = validate_candles(data)  # structural defects raise (fail closed)
    current, window = validated[-1], validated[-policy.lookback - 1:-1]

    prior_high = max(c.high for c in window)
    prior_low = min(c.low for c in window)
    volume_ma = sum(c.volume for c in window) / Decimal(policy.lookback)
    has_volume = volume_ma == 0 or current.volume >= volume_ma * policy.volume_multiplier
    if not has_volume:
        return None

    direction: str | None = None
    if current.close > prior_high:
        direction = "long"
    elif current.close < prior_low:
        direction = "short"
    if direction is None:
        return None
    if policy.market_type == "spot" and direction == "short":
        return None

    plan = build_risk_plan(
        equity=equity, entry=current.close, atr=atr(validated, 14),
        direction=direction, market_type=policy.market_type,
        symbol=policy.symbol, limits=policy.limits,
        leverage=policy.leverage if policy.market_type == "futures" else Decimal("1"),
    )
    if not plan.eligible:
        return None
    return TradeIntent(
        current.timestamp, plan.direction, plan.quantity,
        abs(plan.entry - plan.stop_loss),
        abs(plan.take_profit - plan.entry),
        market_type=plan.market_type,
    )


def breakout_strategy(policy: BreakoutPolicy):
    """Registry adapter: freeze a policy into the (history, ts, equity) protocol."""
    def strategy(history: tuple[Candle, ...], timestamp: int, equity: Decimal) -> TradeIntent | None:
        data = tuple(history)
        if not data or data[-1].timestamp != timestamp:
            raise ValueError("history must end at the timestamp under evaluation")
        return breakout_intent(data, equity=equity, policy=policy)
    return strategy
