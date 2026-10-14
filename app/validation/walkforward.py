"""Chained walk-forward evaluation over the existing backtest engine.

Each window is evaluated in strict chronological order with equity carried
from the previous window. The caller supplies an ``intent_factory`` that
receives ONLY the window's train candles (for fitting) and the test candles'
timestamps (the known schedule) — never test prices — so intents for the test
segment cannot be shaped by information that was unavailable at decision
time. Execution semantics (next-candle open, stop-first ambiguity, fees and
slippage) come from ``run_backtest`` unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterable, Mapping

from app.strategy.indicators import Candle, validate_candles
from app.validation.backtest import (
    BacktestConfig,
    BacktestResult,
    ClosedTrade,
    TradeIntent,
    run_backtest,
)
from app.validation.splits import walk_forward_windows

IntentFactory = Callable[[tuple[Candle, ...], tuple[int, ...]], Mapping[int, TradeIntent]]
"""Given train candles and allowed test timestamps, return test-segment intents."""


@dataclass(frozen=True, slots=True)
class WindowResult:
    index: int
    train_size: int
    test_size: int
    initial_equity: Decimal
    final_equity: Decimal
    net_pnl: Decimal
    trade_count: int
    max_drawdown_fraction: Decimal


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    windows: tuple[WindowResult, ...]
    initial_equity: Decimal
    final_equity: Decimal
    net_pnl: Decimal
    total_trades: int
    win_count: int
    loss_count: int
    total_fees: Decimal
    max_drawdown_fraction: Decimal
    trades: tuple[ClosedTrade, ...]
    result_per_window: tuple[BacktestResult, ...]
    notes: tuple[str, ...]


def evaluate_walk_forward(
    candles: Iterable[Candle],
    *,
    factory: IntentFactory,
    train_size: int,
    test_size: int,
    step: int | None = None,
    anchored: bool = True,
    config: BacktestConfig = BacktestConfig(),
) -> WalkForwardReport:
    """Evaluate an intent policy out-of-sample across chronological windows.

    ``factory`` must be deterministic and may fit only on the train candles it
    receives. Intents keyed to the final candle of a test window are rejected
    by ``run_backtest`` (next-open execution leaves no room), so factories
    should skip the last test timestamp. Raises ValueError if no full window
    fits the data.
    """
    data = validate_candles(tuple(candles), min_count=1)
    windows = walk_forward_windows(
        data, train_size=train_size, test_size=test_size, step=step, anchored=anchored
    )
    if not windows:
        raise ValueError(
            f"no full walk-forward window fits {len(data)} candles "
            f"(train_size={train_size}, test_size={test_size})"
        )

    carried = config.initial_equity
    peak = carried
    max_drawdown = Decimal("0")
    window_results: list[WindowResult] = []
    raw_results: list[BacktestResult] = []
    trades: list[ClosedTrade] = []
    total_fees = Decimal("0")

    for window in windows:
        allowed_timestamps = tuple(c.timestamp for c in window.test)
        intents = factory(window.train, allowed_timestamps)
        result = run_backtest(
            window.test,
            intents,
            BacktestConfig(
                initial_equity=carried,
                fee_rate=config.fee_rate,
                slippage_rate=config.slippage_rate,
            ),
        )
        raw_results.append(result)
        trades.extend(result.trades)
        total_fees += result.total_fees
        window_results.append(WindowResult(
            window.index, len(window.train), len(window.test),
            carried, result.final_equity, result.net_pnl,
            result.trade_count, result.max_drawdown_fraction,
        ))
        for mark in result.equity_marks:
            peak = max(peak, mark)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - mark) / peak)
        carried = result.final_equity

    net_pnl = carried - config.initial_equity
    wins = sum(1 for trade in trades if trade.net_pnl > 0)
    losses = sum(1 for trade in trades if trade.net_pnl < 0)
    return WalkForwardReport(
        tuple(window_results), config.initial_equity, carried, net_pnl,
        len(trades), wins, losses, total_fees, max_drawdown,
        tuple(trades), tuple(raw_results),
        ("Offline simulation only; no exchange connection or orders.",
         "Each window is evaluated in chronological order with equity carried forward.",
         "The intent factory receives only train candles and test timestamps, not test prices.",
         "Execution semantics (next-candle open, stop-first, fees, slippage) match run_backtest.",
         "Overall max drawdown is computed on the chained equity-mark series."),
    )
