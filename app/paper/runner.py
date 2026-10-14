"""Wire the trend policy through the risk engine into the isolated paper engine.

Pure orchestration over tested components: for each candle the policy sees
only trailing history (no future), sizing uses the current marked equity, and
``PaperEngine`` applies next-open fills with realistic costs. A halted engine
keeps step semantics (its decision log records every rejection); ``finish``
closes any residual position at the final candle close. No network, no orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from app.paper.portfolio import Decision, PaperConfig, PaperEngine
from app.strategy.indicators import Candle, validate_candles
from app.strategy.policy import TrendPolicy, trend_intent


@dataclass(frozen=True, slots=True)
class PaperSessionReport:
    candles_processed: int
    decisions: tuple[Decision, ...]
    initial_equity: Decimal
    final_equity: Decimal
    total_fees: Decimal
    trade_count: int
    net_pnl: Decimal
    halted: bool
    notes: tuple[str, ...]


def run_paper_session(
    candles: Iterable[Candle],
    *,
    policy: TrendPolicy = TrendPolicy(),
    engine: PaperEngine | None = None,
    mark: str = "close",
) -> PaperSessionReport:
    """Replay candles into a paper engine via the risk-sized trend policy.

    Deterministic: identical inputs produce an identical report. The engine
    may be pre-halted by the caller to exercise the emergency-stop gate.
    """
    if mark != "close":
        raise ValueError("only close-marked equity is supported")
    data = validate_candles(tuple(candles), min_count=2)
    active = engine if engine is not None else PaperEngine(PaperConfig())
    history: list[Candle] = []
    for candle in data:
        history.append(candle)
        equity = active.equity_at(candle.close)
        intent = trend_intent(tuple(history), equity=equity, policy=policy)
        active.step(candle, intent)
    active.finish(data[-1])
    final_equity = active.equity_at(data[-1].close)
    return PaperSessionReport(
        len(data), active.decisions, active.config.initial_cash, final_equity,
        active.total_fees, len(active.trades), final_equity - active.config.initial_cash,
        active.halted,
        ("Paper-trading simulation only; the engine has no exchange or network access.",
         "Intents fill at the next candle open with adverse slippage and dual-side fees.",
         "Position sizing reuses build_risk_plan hard caps via the trend policy.",
         "Policies whose notional cap binds must leave fee headroom (see app.strategy.policy)."),
    )
