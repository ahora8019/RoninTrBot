"""Deterministic, offline OHLCV backtesting primitives.

Signals are supplied by the caller and are acted on at the *next* candle open,
preventing same-candle look-ahead. If a candle touches both stop and target,
the stop is assumed first (conservative ambiguity handling). No exchange I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

from app.strategy.indicators import Candle, validate_candles


def _d(value: object, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field} must be a finite decimal") from None
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return result


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_equity: Decimal = Decimal("1000")
    fee_rate: Decimal = Decimal("0.001")
    slippage_rate: Decimal = Decimal("0.0005")

    def __post_init__(self) -> None:
        for field in ("initial_equity", "fee_rate", "slippage_rate"):
            value = _d(getattr(self, field), field)
            object.__setattr__(self, field, value)
        if self.initial_equity <= 0:
            raise ValueError("initial_equity must be greater than zero")
        if not Decimal("0") <= self.fee_rate < Decimal("1"):
            raise ValueError("fee_rate must be in [0, 1)")
        if not Decimal("0") <= self.slippage_rate < Decimal("1"):
            raise ValueError("slippage_rate must be in [0, 1)")


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """A signal at a candle close; distances are absolute price units."""
    signal_timestamp: int
    direction: str
    quantity: Decimal
    stop_distance: Decimal
    target_distance: Decimal

    def __post_init__(self) -> None:
        if isinstance(self.signal_timestamp, bool) or not isinstance(self.signal_timestamp, int):
            raise ValueError("signal_timestamp must be an integer Unix timestamp")
        direction = self.direction.strip().lower() if isinstance(self.direction, str) else ""
        if direction not in {"long", "short"}:
            raise ValueError("direction must be 'long' or 'short'")
        object.__setattr__(self, "direction", direction)
        for field in ("quantity", "stop_distance", "target_distance"):
            value = _d(getattr(self, field), field)
            object.__setattr__(self, field, value)
            if value <= 0:
                raise ValueError(f"{field} must be greater than zero")


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    direction: str
    signal_timestamp: int
    entry_timestamp: int
    exit_timestamp: int
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    exit_reason: str


@dataclass(frozen=True, slots=True)
class BacktestResult:
    initial_equity: Decimal
    final_equity: Decimal
    net_pnl: Decimal
    return_fraction: Decimal
    max_drawdown_fraction: Decimal
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: Decimal
    total_fees: Decimal
    trades: tuple[ClosedTrade, ...]
    notes: tuple[str, ...]


def run_backtest(
    candles: Iterable[Candle],
    intents: Mapping[int, TradeIntent],
    config: BacktestConfig = BacktestConfig(),
) -> BacktestResult:
    """Run a single-position, next-open backtest over strictly ordered candles.

    Intent keys and TradeIntent.signal_timestamp must match a candle timestamp.
    Only one position may be open; signals while a position is open are ignored.
    Remaining open positions are closed at the final close, with adverse slippage.
    """
    data = validate_candles(candles, min_count=2)
    by_timestamp = {c.timestamp: i for i, c in enumerate(data)}
    for timestamp, intent in intents.items():
        if timestamp not in by_timestamp or intent.signal_timestamp != timestamp:
            raise ValueError("every intent must match a candle timestamp and its key")
        if by_timestamp[timestamp] >= len(data) - 1:
            raise ValueError("the final candle cannot host a signal without a next candle")
        if not isinstance(intent, TradeIntent):
            raise TypeError("intents must contain TradeIntent records")

    equity = config.initial_equity
    peak = equity
    max_drawdown = Decimal("0")
    trades: list[ClosedTrade] = []
    total_fees = Decimal("0")
    position: dict[str, object] | None = None

    for i, candle in enumerate(data):
        if position is not None:
            direction = str(position["direction"])
            stop = position["stop"]
            target = position["target"]
            assert isinstance(stop, Decimal) and isinstance(target, Decimal)
            exit_price: Decimal | None = None
            reason = ""
            if direction == "long":
                if candle.open <= stop:
                    exit_price, reason = candle.open, "stop_gap"
                elif candle.open >= target:
                    exit_price, reason = candle.open, "target_gap"
                elif candle.low <= stop and candle.high >= target:
                    exit_price, reason = stop, "stop_first_both_touched"
                elif candle.low <= stop:
                    exit_price, reason = stop, "stop"
                elif candle.high >= target:
                    exit_price, reason = target, "target"
            else:
                if candle.open >= stop:
                    exit_price, reason = candle.open, "stop_gap"
                elif candle.open <= target:
                    exit_price, reason = candle.open, "target_gap"
                elif candle.high >= stop and candle.low <= target:
                    exit_price, reason = stop, "stop_first_both_touched"
                elif candle.high >= stop:
                    exit_price, reason = stop, "stop"
                elif candle.low <= target:
                    exit_price, reason = target, "target"
            if i == len(data) - 1 and exit_price is None:
                exit_price, reason = candle.close, "end_of_data"
            if exit_price is not None:
                qty = position["quantity"]
                entry = position["entry"]
                assert isinstance(qty, Decimal) and isinstance(entry, Decimal)
                slipped_exit = exit_price * (Decimal("1") - config.slippage_rate if direction == "long" else Decimal("1") + config.slippage_rate)
                gross = (slipped_exit - entry) * qty if direction == "long" else (entry - slipped_exit) * qty
                entry_fee = position["entry_fee"]
                assert isinstance(entry_fee, Decimal)
                exit_fee = slipped_exit * qty * config.fee_rate
                fees = entry_fee + exit_fee
                net = gross - fees
                # Entry fee was deducted when the position opened; realize only gross PnL minus exit fee here.
                equity += gross - exit_fee
                trades.append(ClosedTrade(direction, int(position["signal_timestamp"]),
                    int(position["entry_timestamp"]), candle.timestamp, entry, slipped_exit,
                    qty, gross, fees, net, reason))
                position = None
                peak = max(peak, equity)
                if peak > 0:
                    max_drawdown = max(max_drawdown, (peak - equity) / peak)

        intent = intents.get(candle.timestamp)
        if position is None and intent is not None:
            next_candle = data[i + 1]
            raw_entry = next_candle.open
            entry = raw_entry * (Decimal("1") + config.slippage_rate if intent.direction == "long" else Decimal("1") - config.slippage_rate)
            if entry <= 0:
                continue
            stop = entry - intent.stop_distance if intent.direction == "long" else entry + intent.stop_distance
            target = entry + intent.target_distance if intent.direction == "long" else entry - intent.target_distance
            if stop <= 0 or target <= 0:
                continue
            entry_fee = entry * intent.quantity * config.fee_rate
            # Reject an entry whose fee alone exceeds available equity.
            if entry * intent.quantity + entry_fee > max(equity, Decimal("0")):
                continue
            equity -= entry_fee
            total_fees += entry_fee
            position = {"direction": intent.direction, "quantity": intent.quantity,
                        "entry": entry, "stop": stop, "target": target,
                        "signal_timestamp": intent.signal_timestamp,
                        "entry_timestamp": next_candle.timestamp, "entry_fee": entry_fee}

    # Equity is cash/equity after realized PnL; this is a closed-trade report.
    net_pnl = equity - config.initial_equity
    wins = sum(1 for trade in trades if trade.net_pnl > 0)
    losses = sum(1 for trade in trades if trade.net_pnl < 0)
    count = len(trades)
    return BacktestResult(
        config.initial_equity, equity, net_pnl, net_pnl / config.initial_equity,
        max_drawdown, count, wins, losses,
        Decimal(wins) / Decimal(count) if count else Decimal("0"),
        total_fees, tuple(trades),
        ("Offline simulation only; no exchange connection or orders.",
         "Signals execute no earlier than the next candle open.",
         "If stop and target are both touched in one candle, stop is assumed first.",
         "Open positions are closed at the final candle close with slippage.",
         "Results exclude funding, borrow costs, spread variability, and partial fills."),
    )
