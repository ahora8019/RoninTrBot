from __future__ import annotations

import random

import pytest

from app.scanner import (
    DecisionStatus,
    MarketClass,
    SymbolCandidate,
    SymbolEvaluation,
    scan_cycle,
)


def test_scan_visits_every_symbol_and_continues_after_no_trade() -> None:
    seen: list[str] = []

    def evaluate(candidate: SymbolCandidate) -> SymbolEvaluation:
        seen.append(candidate.symbol)
        if candidate.symbol == "BADUSDT":
            return SymbolEvaluation(MarketClass.UNSUITABLE, reason="spread too wide")
        return SymbolEvaluation(MarketClass.SUITABLE, setup_valid=False, reason="no setup")

    candidates = [
        SymbolCandidate("BTCUSDT"),
        SymbolCandidate("BADUSDT"),
        SymbolCandidate("ETHUSDT"),
    ]
    decisions = scan_cycle(candidates, evaluate, shuffle=False)

    assert seen == ["BTCUSDT", "BADUSDT", "ETHUSDT"]
    assert [decision.status for decision in decisions] == [
        DecisionStatus.NO_TRADE,
        DecisionStatus.NO_TRADE,
        DecisionStatus.NO_TRADE,
    ]
    assert len(decisions) == 3


def test_risky_market_requires_valid_setup_and_all_risk_gates() -> None:
    candidates = [
        SymbolCandidate("RISK1USDT"),
        SymbolCandidate("RISK2USDT"),
    ]

    def evaluate(candidate: SymbolCandidate) -> SymbolEvaluation:
        if candidate.symbol == "RISK1USDT":
            return SymbolEvaluation(
                MarketClass.RISKY, setup_valid=True, risk_gates_passed=False
            )
        return SymbolEvaluation(
            MarketClass.RISKY, setup_valid=True, risk_gates_passed=True
        )

    decisions = scan_cycle(candidates, evaluate, shuffle=False)
    assert decisions[0].status is DecisionStatus.NO_TRADE
    assert decisions[1].status is DecisionStatus.SETUP_FOUND


def test_one_symbol_error_does_not_stop_remaining_symbols() -> None:
    seen: list[str] = []

    def evaluate(candidate: SymbolCandidate) -> SymbolEvaluation:
        seen.append(candidate.symbol)
        if candidate.symbol == "BROKENUSDT":
            raise TimeoutError("simulated market data timeout")
        return SymbolEvaluation(
            MarketClass.SUITABLE, setup_valid=True, risk_gates_passed=True
        )

    decisions = scan_cycle(
        [
            SymbolCandidate("BROKENUSDT"),
            SymbolCandidate("BTCUSDT"),
        ],
        evaluate,
        shuffle=False,
    )
    assert seen == ["BROKENUSDT", "BTCUSDT"]
    assert decisions[0].status is DecisionStatus.ERROR_SKIPPED
    assert decisions[0].reason == "evaluation error: TimeoutError"
    assert decisions[1].status is DecisionStatus.SETUP_FOUND


def test_randomized_order_still_visits_each_candidate_once() -> None:
    candidates = [SymbolCandidate(f"COIN{i}USDT") for i in range(20)]
    seen: list[str] = []

    def evaluate(candidate: SymbolCandidate) -> SymbolEvaluation:
        seen.append(candidate.symbol)
        return SymbolEvaluation(MarketClass.UNSUITABLE)

    decisions = scan_cycle(candidates, evaluate, rng=random.Random(42))
    assert len(decisions) == len(candidates)
    assert set(seen) == {candidate.symbol for candidate in candidates}
    assert len(seen) == len(set(seen))


def test_duplicate_market_symbol_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        scan_cycle(
            [SymbolCandidate("btcusdt"), SymbolCandidate("BTCUSDT")],
            lambda _: SymbolEvaluation(MarketClass.UNSUITABLE),
        )


def test_invalid_candidate_market_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="market_type"):
        SymbolCandidate("BTCUSDT", market_type="options")
