"""Fail-closed multi-symbol scan cycle for RoninTrBot.

This module orchestrates decisions only: it never submits orders or calls the network.
The caller supplies market candidates and a per-symbol evaluator.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable


class MarketClass(str, Enum):
    SUITABLE = "SUITABLE"
    RISKY = "RISKY"
    UNSUITABLE = "UNSUITABLE"


class DecisionStatus(str, Enum):
    SETUP_FOUND = "SETUP_FOUND"
    NO_TRADE = "NO_TRADE"
    ERROR_SKIPPED = "ERROR_SKIPPED"


@dataclass(frozen=True, slots=True)
class SymbolCandidate:
    symbol: str
    market_type: str = "spot"

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be non-empty")
        normalized = self.market_type.strip().lower() if isinstance(self.market_type, str) else ""
        if normalized not in {"spot", "futures"}:
            raise ValueError("market_type must be 'spot' or 'futures'")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        object.__setattr__(self, "market_type", normalized)


@dataclass(frozen=True, slots=True)
class SymbolEvaluation:
    classification: MarketClass
    setup_valid: bool = False
    risk_gates_passed: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.classification, MarketClass):
            raise ValueError("classification must be a MarketClass")
        if not isinstance(self.setup_valid, bool) or not isinstance(self.risk_gates_passed, bool):
            raise ValueError("setup_valid and risk_gates_passed must be booleans")
        if not isinstance(self.reason, str):
            raise ValueError("reason must be a string")


@dataclass(frozen=True, slots=True)
class ScanDecision:
    symbol: str
    market_type: str
    status: DecisionStatus
    reason: str


Evaluator = Callable[[SymbolCandidate], SymbolEvaluation]


def scan_cycle(
    candidates: Iterable[SymbolCandidate],
    evaluator: Evaluator,
    *,
    shuffle: bool = True,
    rng: random.Random | None = None,
) -> tuple[ScanDecision, ...]:
    """Evaluate each candidate once per cycle, continuing after NO_TRADE or errors.

    Randomization changes order only; every candidate in this snapshot is still
    visited at most once, preventing random selection from starving symbols.
    SETUP_FOUND means a paper-only candidate setup passed the evaluator's gates;
    it does not mean an order was created or submitted.
    """
    queue = list(candidates)
    if any(not isinstance(item, SymbolCandidate) for item in queue):
        raise TypeError("candidates must contain SymbolCandidate records")
    if len({(item.market_type, item.symbol) for item in queue}) != len(queue):
        raise ValueError("duplicate market_type/symbol in one scan cycle")
    if shuffle:
        (rng or random).shuffle(queue)

    decisions: list[ScanDecision] = []
    for candidate in queue:
        try:
            result = evaluator(candidate)
            if not isinstance(result, SymbolEvaluation):
                raise TypeError("evaluator must return SymbolEvaluation")
            if result.classification is MarketClass.UNSUITABLE:
                status = DecisionStatus.NO_TRADE
                reason = result.reason or "market is unsuitable"
            elif result.classification is MarketClass.RISKY:
                if result.setup_valid and result.risk_gates_passed:
                    status = DecisionStatus.SETUP_FOUND
                    reason = result.reason or "risky market passed stricter risk gates"
                else:
                    status = DecisionStatus.NO_TRADE
                    reason = result.reason or "risky market failed stricter risk gates"
            elif result.setup_valid and result.risk_gates_passed:
                status = DecisionStatus.SETUP_FOUND
                reason = result.reason or "setup and risk gates passed"
            else:
                status = DecisionStatus.NO_TRADE
                reason = result.reason or "no valid setup or risk gate failed"
        except Exception as exc:
            # A bad symbol must not terminate the rest of the scan cycle.
            status = DecisionStatus.ERROR_SKIPPED
            reason = f"evaluation error: {type(exc).__name__}"

        decisions.append(ScanDecision(
            symbol=candidate.symbol,
            market_type=candidate.market_type,
            status=status,
            reason=reason,
        ))
    return tuple(decisions)
