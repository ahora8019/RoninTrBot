"""Pure risk calculations for paper analysis; never submits or manages orders."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN


def _d(value: object, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field} must be a finite decimal") from None
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return result


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Explicit hard limits; values are policy inputs, not trading advice."""
    risk_fraction: Decimal = Decimal("0.01")
    max_risk_fraction: Decimal = Decimal("0.02")
    atr_stop_multiple: Decimal = Decimal("2")
    reward_risk_ratio: Decimal = Decimal("2")
    max_notional_fraction: Decimal = Decimal("1")
    max_leverage: Decimal = Decimal("1")
    quantity_step: Decimal = Decimal("0.000001")

    def __post_init__(self) -> None:
        names = ("risk_fraction", "max_risk_fraction", "atr_stop_multiple",
                 "reward_risk_ratio", "max_notional_fraction", "max_leverage", "quantity_step")
        for name in names:
            value = _d(getattr(self, name), name)
            object.__setattr__(self, name, value)
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if self.risk_fraction > self.max_risk_fraction:
            raise ValueError("risk_fraction must not exceed max_risk_fraction")
        if self.max_risk_fraction > Decimal("0.02"):
            raise ValueError("max_risk_fraction cannot exceed the hard 2% cap")
        if self.max_notional_fraction > Decimal("1"):
            raise ValueError("max_notional_fraction cannot exceed 1.0")
        if self.max_leverage > Decimal("3"):
            raise ValueError("max_leverage cannot exceed the hard 3x cap")


@dataclass(frozen=True, slots=True)
class RiskPlan:
    market_type: str
    symbol: str
    direction: str
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    quantity: Decimal
    notional: Decimal
    risk_amount: Decimal
    risk_fraction: Decimal
    reward_risk_ratio: Decimal
    eligible: bool
    reasons: tuple[str, ...]


def build_risk_plan(
    *, equity: Decimal | str | int, entry: Decimal | str | int,
    atr: Decimal | str | int, direction: str, market_type: str,
    symbol: str, limits: RiskLimits = RiskLimits(),
    leverage: Decimal | str | int = Decimal("1"),
) -> RiskPlan:
    """Calculate a conservative theoretical plan. It does not create an order."""
    equity_d, entry_d, atr_d, leverage_d = (
        _d(equity, "equity"), _d(entry, "entry"), _d(atr, "atr"), _d(leverage, "leverage")
    )
    side = direction.strip().lower() if isinstance(direction, str) else ""
    market = market_type.strip().lower() if isinstance(market_type, str) else ""
    if equity_d <= 0 or entry_d <= 0:
        raise ValueError("equity and entry must be greater than zero")
    if atr_d <= 0:
        raise ValueError("atr must be greater than zero")
    if side not in {"long", "short"}:
        raise ValueError("direction must be 'long' or 'short'")
    if market not in {"spot", "futures"}:
        raise ValueError("market_type must be 'spot' or 'futures'")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be non-empty")
    if market == "spot" and side == "short":
        raise ValueError("spot short plans are unsupported")
    if leverage_d < 1 or leverage_d > limits.max_leverage:
        raise ValueError("leverage is outside configured hard limits")
    if market == "spot" and leverage_d != 1:
        raise ValueError("spot leverage must be 1")

    stop_distance = atr_d * limits.atr_stop_multiple
    if stop_distance >= entry_d:
        raise ValueError("ATR stop distance must be smaller than entry price")
    stop = entry_d - stop_distance if side == "long" else entry_d + stop_distance
    target_distance = stop_distance * limits.reward_risk_ratio
    target = entry_d + target_distance if side == "long" else entry_d - target_distance

    risk_budget = equity_d * limits.risk_fraction
    raw_quantity = risk_budget / stop_distance
    notional_cap = equity_d * limits.max_notional_fraction * leverage_d
    quantity = min(raw_quantity, notional_cap / entry_d)
    quantity = (quantity / limits.quantity_step).to_integral_value(rounding=ROUND_DOWN) * limits.quantity_step
    if quantity <= 0:
        raise ValueError("calculated quantity is below the configured quantity step")

    notional = quantity * entry_d
    risk_amount = quantity * stop_distance
    actual_risk_fraction = risk_amount / equity_d
    reasons = [
        f"stop distance is {limits.atr_stop_multiple} x ATR",
        f"target distance is {limits.reward_risk_ratio} x stop distance",
        "quantity is capped by risk budget, notional limit, and quantity step",
        "calculation only: no order was created or submitted",
    ]
    eligible = actual_risk_fraction <= limits.max_risk_fraction and notional <= notional_cap
    if not eligible:
        reasons.append("hard risk checks failed; do not use this plan")
    return RiskPlan(
        market, symbol.strip().upper(), side, entry_d, stop, target, quantity,
        notional, risk_amount, actual_risk_fraction, limits.reward_risk_ratio,
        eligible, tuple(reasons),
    )
