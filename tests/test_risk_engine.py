import pytest

from app.core.market_scanner import MarketType
from app.core.risk_engine import (
    RiskDecisionStatus,
    RiskPolicy,
    assess_paper_position,
)


def assess(**overrides):
    args = dict(
        balance=100.0,
        entry_price=100.0,
        stop_price=95.0,
        take_profit_price=110.0,
        market_type=MarketType.SPOT,
    )
    args.update(overrides)
    return assess_paper_position(**args)


def test_position_size_respects_balance_cap_and_paper_only():
    result = assess()
    assert result.status is RiskDecisionStatus.APPROVED_FOR_PAPER
    assert result.notional <= 20.0
    assert result.quantity > 0
    assert result.estimated_loss_at_stop <= result.risk_budget


def test_zero_balance_fails_closed():
    result = assess(balance=0)
    assert result.status is RiskDecisionStatus.NO_TRADE
    assert result.quantity == 0


def test_invalid_long_setup_fails_closed():
    result = assess(stop_price=101)
    assert result.status is RiskDecisionStatus.NO_TRADE


def test_risk_fraction_above_guardrail_fails_closed():
    result = assess(policy=RiskPolicy(risk_fraction=0.05))
    assert result.status is RiskDecisionStatus.NO_TRADE


def test_futures_does_not_enable_leverage_by_default():
    result = assess(market_type=MarketType.FUTURES)
    assert result.status is RiskDecisionStatus.APPROVED_FOR_PAPER
    assert result.reasons[0] == "paper_only"


def test_non_finite_input_fails_closed():
    result = assess(entry_price=float("nan"))
    assert result.status is RiskDecisionStatus.NO_TRADE
