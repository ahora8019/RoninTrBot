"""Conservative, exchange-agnostic position sizing for paper trading only.

This module computes risk-based sizing; it never submits orders. Spot and futures
must call it with separate policies and independently validated exchange constraints.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math

from app.core.market_scanner import MarketType


class RiskDecisionStatus(str, Enum):
    APPROVED_FOR_PAPER = "APPROVED_FOR_PAPER"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    risk_fraction: float = 0.01
    max_position_fraction: float = 0.20
    max_leverage: float = 1.0
    estimated_cost_fraction: float = 0.001


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    status: RiskDecisionStatus
    market_type: MarketType
    quantity: float
    notional: float
    risk_budget: float
    estimated_loss_at_stop: float
    stop_price: float
    take_profit_price: float
    reasons: tuple[str, ...]


def assess_paper_position(
    *,
    balance: float,
    entry_price: float,
    stop_price: float,
    take_profit_price: float,
    market_type: MarketType,
    policy: RiskPolicy = RiskPolicy(),
) -> RiskAssessment:
    """Size a hypothetical position from balance and stop distance.

    Long-only sizing for the initial safe implementation. Futures direction,
    margin, liquidation distance, funding and short positions require a later,
    explicit contract-aware layer and are deliberately not inferred here.
    """
    values = (
        balance, entry_price, stop_price, take_profit_price,
        policy.risk_fraction, policy.max_position_fraction,
        policy.max_leverage, policy.estimated_cost_fraction,
    )
    if any(not math.isfinite(v) for v in values):
        return _no_trade(market_type, stop_price, take_profit_price, "non_finite_input")
    if balance <= 0 or entry_price <= 0 or stop_price <= 0 or take_profit_price <= 0:
        return _no_trade(market_type, stop_price, take_profit_price, "prices_and_balance_must_be_positive")
    if not (0 < policy.risk_fraction <= 0.02):
        return _no_trade(market_type, stop_price, take_profit_price, "risk_fraction_outside_allowed_range")
    if not (0 < policy.max_position_fraction <= 1):
        return _no_trade(market_type, stop_price, take_profit_price, "invalid_max_position_fraction")
    if policy.max_leverage < 1 or policy.max_leverage > 1:
        return _no_trade(market_type, stop_price, take_profit_price, "leverage_not_enabled_in_initial_risk_engine")
    if not (0 <= policy.estimated_cost_fraction < 0.05):
        return _no_trade(market_type, stop_price, take_profit_price, "invalid_cost_assumption")
    if not stop_price < entry_price < take_profit_price:
        return _no_trade(market_type, stop_price, take_profit_price, "initial_engine_requires_long_setup_with_stop_below_entry")

    risk_budget = balance * policy.risk_fraction
    loss_fraction = (entry_price - stop_price) / entry_price + policy.estimated_cost_fraction
    if loss_fraction <= 0:
        return _no_trade(market_type, stop_price, take_profit_price, "invalid_stop_distance")
    risk_limited_notional = risk_budget / loss_fraction
    position_cap = balance * policy.max_position_fraction
    notional = min(risk_limited_notional, position_cap)
    quantity = notional / entry_price
    estimated_loss = notional * loss_fraction
    return RiskAssessment(
        status=RiskDecisionStatus.APPROVED_FOR_PAPER,
        market_type=market_type,
        quantity=quantity,
        notional=notional,
        risk_budget=risk_budget,
        estimated_loss_at_stop=estimated_loss,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        reasons=("paper_only", "position_capped", "cost_estimate_included"),
    )


def _no_trade(
    market_type: MarketType,
    stop_price: float,
    take_profit_price: float,
    reason: str,
) -> RiskAssessment:
    return RiskAssessment(
        status=RiskDecisionStatus.NO_TRADE,
        market_type=market_type,
        quantity=0.0,
        notional=0.0,
        risk_budget=0.0,
        estimated_loss_at_stop=0.0,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        reasons=(reason,),
    )
