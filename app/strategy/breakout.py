"""Breakout strategy: Donchian-channel close-break with a volume filter.

Second strategy in the multi-strategy design (router maps the "range" regime
here: ranges are where breaks originate). Pure, offline, fail-closed: no
setup -> None, unsupported side -> None, ineligible risk plan -> None.

Contract mirrors ``app.strategy.policy.trend_intent``: callers pass trailing
history ending at the decision candle (never future candles); any emitted
intent is keyed to that candle and fills at the NEXT candle's open in the
backtest and paper engines. Channel and volume window both EXCLUDE the signal
candle — no look-ahead in the setup itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.risk.engine import RiskLimits, build_risk_plan
from app.strategy.indicators import Candle, sma, validate_candles
from app.validation.backtest import TradeIntent


@dataclass(frozen=True, slots=True)
class BreakoutPolicy:
    """Config for breakout_intent(); explicit policy inputs, not advice."""
    lookback: int = 20                 # Donchian channel length (prior candles)
    volume_lookback: int = 20          # volume SMA window (prior candles)
    volume_multiplier: Decimal = Decimal("1.5")  # breakout volume >= k x volume SMA
    market_type: str = "futures"
    symbol: str = "GENERIC"
    limits: RiskLimits = RiskLimits()
    leverage: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if isinstance(self.lookback, bool) or not isinstance(self.lookback, int) \
                or self.lookback < 2:
            raise ValueError("lookback must be an integer >= 2")
        if isinstance(self.volume_lookback, bool) or not isinstance(self.volume_lookback, int) \
                or self.volume_lookback < 1:
            raise ValueError("volume_lookback must be an integer >= 1")
        try:
            multiplier = Decimal(str(self.volume_multiplier))
        except Exception:
            raise ValueError("volume_multiplier must be a finite decimal") from None
        if not multiplier.is_finite() or multiplier <= 0:
            raise ValueError("volume_multiplier must be a positive finite decimal")
        object.__setattr__(self, "volume_multiplier", multiplier)
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
        # channel/volume windows exclude the signal candle (>= lookback pric + 1),
        # and ATR(14) needs 15 candles overall.
        return max(self.lookback + 1, self.volume_lookback + 1, 15)


def breakout_intent(
    candles: tuple[Candle, ...],
    *,
    equity: Decimal,
    policy: BreakoutPolicy = BreakoutPolicy(),
) -> TradeIntent | None:
    """Return a risk-sized intent on a volume-confirmed channel break, or None.

    Long setup: signal close ABOVE the prior ``lookback`` candles' high AND
    signal volume >= ``volume_multiplier`` x the prior-window volume SMA; the
    short setup is symmetric. Spot shorts fail closed to None (mirrors the
    risk engine). Structural data defects raise (fail-closed).
    """
    data = tuple(candles)
    if len(data) < policy.min_candles:
        return None
    validated = validate_candles(data)
    signal = validated[-1]
    prior = validated[-(policy.lookback + 1):-1]
    prior_high = max(c.high for c in prior)
    prior_low = min(c.low for c in prior)
    volumes = [c.volume for c in validated[-(policy.volume_lookback + 1):-1]]

    direction: str | None = None
    if signal.close > prior_high:
        direction = "long"
    elif signal.close < prior_low:
        direction = "short"
    if direction is None:
        return None
    if signal.volume < policy.volume_multiplier * sma(volumes, policy.volume_lookback):
        return None  # unconfirmed break
    if policy.market_type == "spot" and direction == "short":
        return None

    from app.strategy.indicators import atr  # local import keeps module surface small
    plan = build_risk_plan(
        equity=equity, entry=signal.close, atr=atr(validated, 14),
        direction=direction, market_type=policy.market_type,
        symbol=policy.symbol, limits=policy.limits,
        leverage=policy.leverage if policy.market_type == "futures" else Decimal("1"),
    )
    if not plan.eligible:
        return None
    return TradeIntent(
        signal.timestamp, plan.direction, plan.quantity,
        abs(plan.entry - plan.stop_loss),
        abs(plan.take_profit - plan.entry),
        market_type=plan.market_type,
    )


def breakout_strategy(
    candles: tuple[Candle, ...],
    equity: Decimal,
    policy: BreakoutPolicy = BreakoutPolicy(),
) -> TradeIntent | None:
    """Registry-adapter form: exact ``(history, equity) -> TradeIntent | None``."""
    return breakout_intent(candles, equity=equity, policy=policy)
