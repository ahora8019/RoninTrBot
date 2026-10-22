"""Deterministic metrics for comparing isolated paper-strategy runs.

This module is analysis-only: it does not select a live strategy, alter risk
limits, fetch market data, or place exchange orders.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ClosedTradeResult:
    """Minimal, strategy-agnostic result for one fully closed simulated trade."""

    net_pnl: float
    entry_time: int
    exit_time: int


@dataclass(frozen=True, slots=True)
class StrategyMetrics:
    trades: int
    wins: int
    losses: int
    net_pnl: float
    profit_factor: float | None
    win_rate: float
    max_drawdown: float
    expectancy: float
    average_win: float
    average_loss: float


def calculate_metrics(trades: Iterable[ClosedTradeResult], *, starting_balance: float) -> StrategyMetrics:
    """Calculate comparable metrics in chronological order.

    max_drawdown is the largest peak-to-trough decline in the realized-equity
    curve, expressed as a fraction of the prior peak. Starting balance must be
    positive so the ratio is meaningful. Open and rejected trades should not
    be supplied; their PnL is not realized.
    """
    if not math.isfinite(starting_balance) or starting_balance <= 0:
        raise ValueError("starting_balance_must_be_finite_and_positive")

    items = list(trades)
    for trade in items:
        if not math.isfinite(trade.net_pnl):
            raise ValueError("trade_pnl_must_be_finite")
        if trade.exit_time < trade.entry_time:
            raise ValueError("trade_exit_before_entry")

    items.sort(key=lambda trade: (trade.exit_time, trade.entry_time))
    wins = [trade.net_pnl for trade in items if trade.net_pnl > 0]
    losses = [trade.net_pnl for trade in items if trade.net_pnl < 0]
    net_pnl = sum(trade.net_pnl for trade in items)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    equity = starting_balance
    peak = starting_balance
    max_drawdown = 0.0
    for trade in items:
        equity += trade.net_pnl
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

    return StrategyMetrics(
        trades=len(items),
        wins=len(wins),
        losses=len(losses),
        net_pnl=round(net_pnl, 8),
        profit_factor=round(gross_profit / gross_loss, 8) if gross_loss else (math.inf if gross_profit else None),
        win_rate=round(len(wins) / len(items), 8) if items else 0.0,
        max_drawdown=round(max_drawdown, 8),
        expectancy=round(net_pnl / len(items), 8) if items else 0.0,
        average_win=round(gross_profit / len(wins), 8) if wins else 0.0,
        average_loss=round(sum(losses) / len(losses), 8) if losses else 0.0,
    )
