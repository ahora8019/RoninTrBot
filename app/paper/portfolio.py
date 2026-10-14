"""Isolated paper-trading engine with realistic simulated costs.

This module is hermetic by design: it performs no network I/O, depends on no
exchange client, and receives every candle by injection from the caller. It
cannot route an order anywhere because there is nothing to route to.

Execution semantics intentionally mirror ``app.validation.backtest``:
intents accepted at a candle are filled at the NEXT candle's open with
adverse slippage, exits use stop-first ambiguity handling, fees apply on both
entry and exit, and all arithmetic uses Decimal. Differences from the batch
backtest (fill-time affordability and stop/target checks instead of
signal-time) reflect forward simulation: the next open is not known when the
signal is accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.strategy.indicators import Candle
from app.validation.backtest import ClosedTrade, TradeIntent


def _d(value: object, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field} must be a finite decimal") from None
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return result


@dataclass(frozen=True, slots=True)
class PaperConfig:
    initial_cash: Decimal = Decimal("1000")
    fee_rate: Decimal = Decimal("0.001")
    slippage_rate: Decimal = Decimal("0.0005")
    max_notional_fraction: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        for field in ("initial_cash", "fee_rate", "slippage_rate", "max_notional_fraction"):
            value = _d(getattr(self, field), field)
            object.__setattr__(self, field, value)
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be greater than zero")
        if not Decimal("0") <= self.fee_rate < Decimal("1"):
            raise ValueError("fee_rate must be in [0, 1)")
        if not Decimal("0") <= self.slippage_rate < Decimal("1"):
            raise ValueError("slippage_rate must be in [0, 1)")
        if not Decimal("0") < self.max_notional_fraction <= Decimal("1"):
            raise ValueError("max_notional_fraction must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class Decision:
    """One recorded engine action; the decision log is the audit trail."""
    index: int
    candle_timestamp: int
    action: str
    reason: str


@dataclass(frozen=True, slots=True)
class PaperPosition:
    direction: str
    market_type: str
    signal_timestamp: int
    quantity: Decimal
    entry: Decimal
    stop: Decimal
    target: Decimal
    entry_timestamp: int
    entry_fee: Decimal


SNAPSHOT_SCHEMA_VERSION = 2  # bump on snapshot-format changes; restore() rejects unknown newer versions


class PaperEngine:
    """Stateful paper portfolio. One position at a time, fail-closed decisions."""

    def __init__(self, config: PaperConfig = PaperConfig()) -> None:
        self._config = config
        self._cash = config.initial_cash
        self._position: PaperPosition | None = None
        self._pending: TradeIntent | None = None
        self._halt_reason: str | None = None
        self._decisions: list[Decision] = []
        self._trades: list[ClosedTrade] = []
        self._total_fees = Decimal("0")
        self._step_index = 0
        self._last_timestamp: int | None = None

    # --- read-only views -------------------------------------------------
    @property
    def config(self) -> PaperConfig:
        return self._config

    @property
    def cash(self) -> Decimal:
        return self._cash

    @property
    def position(self) -> PaperPosition | None:
        return self._position

    @property
    def pending(self) -> TradeIntent | None:
        return self._pending

    @property
    def halted(self) -> bool:
        return self._halt_reason is not None

    @property
    def decisions(self) -> tuple[Decision, ...]:
        return tuple(self._decisions)

    @property
    def trades(self) -> tuple[ClosedTrade, ...]:
        return tuple(self._trades)

    @property
    def total_fees(self) -> Decimal:
        return self._total_fees

    def equity_at(self, mark_price: Decimal | str | int) -> Decimal:
        """Cash plus unrealized PnL at the mark with adverse exit costs (mirrors the backtest MTM)."""
        mark = _d(mark_price, "mark_price")
        if mark <= 0:
            raise ValueError("mark_price must be greater than zero")
        if self._position is None:
            return self._cash
        position = self._position
        mark_exit = mark * (
            Decimal("1") - self._config.slippage_rate
            if position.direction == "long"
            else Decimal("1") + self._config.slippage_rate
        )
        unrealized = ((mark_exit - position.entry) * position.quantity
                      if position.direction == "long"
                      else (position.entry - mark_exit) * position.quantity)
        estimated_exit_fee = mark_exit * position.quantity * self._config.fee_rate
        return self._cash + unrealized - estimated_exit_fee

    # --- control -----------------------------------------------------------
    def halt(self, reason: str) -> Decision:
        reason = (reason or "").strip() or "manual halt"
        decision = self._record(0, "halted", reason)
        self._halt_reason = reason
        return decision

    def resume(self) -> Decision:
        self._halt_reason = None
        return self._record(0, "resumed", "trading resumed")

    # --- simulation --------------------------------------------------------
    def step(self, candle: Candle, intent: TradeIntent | None = None) -> tuple[Decision, ...]:
        """Advance one candle: fill pending at open, process exits, handle intent."""
        if not isinstance(candle, Candle):
            raise TypeError("step expects a validated Candle")
        if self._last_timestamp is not None and candle.timestamp <= self._last_timestamp:
            raise ValueError("candles must arrive in strictly increasing time order")
        self._last_timestamp = candle.timestamp
        self._step_index += 1

        if self._pending is not None:
            self._fill_pending(candle)
        if self._position is not None:
            self._process_exit(candle)

        if intent is not None:
            if not isinstance(intent, TradeIntent):
                raise TypeError("intents must be TradeIntent records")
            if self._halt_reason is not None:
                self._record(candle.timestamp, "entry_rejected",
                             f"engine halted: {self._halt_reason}")
            elif self._position is not None or self._pending is not None:
                # Mirrors the backtest: a signal while a position is open is ignored.
                self._record(candle.timestamp, "signal_ignored",
                             "position or pending entry already open")
            else:
                self._pending = intent
                self._record(candle.timestamp, "entry_scheduled",
                             f"{intent.market_type} {intent.direction} qty {intent.quantity} "
                             f"fills at next candle open")
        return self.decisions

    def finish(self, candle: Candle) -> Decision | None:
        """Close any open position at the final candle close (end-of-data rule)."""
        if not isinstance(candle, Candle):
            raise TypeError("finish expects a validated Candle")
        if self._position is None:
            return None
        position = self._position
        slipped = candle.close * (
            Decimal("1") - self._config.slippage_rate
            if position.direction == "long"
            else Decimal("1") + self._config.slippage_rate
        )
        self._close(position, candle.timestamp, slipped, "end_of_data")
        self._position = None
        return self._record(candle.timestamp, "closed_final",
                            "open position closed at final candle close")

    # --- internals -----------------------------------------------------------
    def _record(self, timestamp: int, action: str, reason: str) -> Decision:
        decision = Decision(self._step_index, timestamp, action, reason)
        self._decisions.append(decision)
        return decision

    def _fill_pending(self, candle: Candle) -> None:
        intent = self._pending
        self._pending = None
        assert intent is not None
        raw_entry = candle.open
        entry = raw_entry * (
            Decimal("1") + self._config.slippage_rate
            if intent.direction == "long"
            else Decimal("1") - self._config.slippage_rate
        )
        if entry <= 0:
            self._record(candle.timestamp, "entry_rejected", "non-positive entry price")
            return
        stop = entry - intent.stop_distance if intent.direction == "long" else entry + intent.stop_distance
        target = entry + intent.target_distance if intent.direction == "long" else entry - intent.target_distance
        if stop <= 0 or target <= 0:
            self._record(candle.timestamp, "entry_rejected",
                         "stop or target is non-positive at fill price")
            return
        entry_fee = entry * intent.quantity * self._config.fee_rate
        notional_cap = self._cash * self._config.max_notional_fraction
        if entry * intent.quantity + entry_fee > max(notional_cap, Decimal("0")):
            self._record(candle.timestamp, "entry_rejected",
                         "insufficient paper cash (notional cap or fees)")
            return
        self._cash -= entry_fee
        self._total_fees += entry_fee
        self._position = PaperPosition(
            intent.direction, intent.market_type, intent.signal_timestamp,
            intent.quantity, entry, stop, target, candle.timestamp, entry_fee,
        )
        self._record(candle.timestamp, "entry_filled",
                     f"filled at open {entry} (raw {raw_entry}, adverse slippage applied)")

    def _process_exit(self, candle: Candle) -> None:
        position = self._position
        assert position is not None
        exit_price: Decimal | None = None
        reason = ""
        if position.direction == "long":
            if candle.open <= position.stop:
                exit_price, reason = candle.open, "stop_gap"
            elif candle.open >= position.target:
                exit_price, reason = candle.open, "target_gap"
            elif candle.low <= position.stop and candle.high >= position.target:
                exit_price, reason = position.stop, "stop_first_both_touched"
            elif candle.low <= position.stop:
                exit_price, reason = position.stop, "stop"
            elif candle.high >= position.target:
                exit_price, reason = position.target, "target"
        else:
            if candle.open >= position.stop:
                exit_price, reason = candle.open, "stop_gap"
            elif candle.open <= position.target:
                exit_price, reason = candle.open, "target_gap"
            elif candle.high >= position.stop and candle.low <= position.target:
                exit_price, reason = position.stop, "stop_first_both_touched"
            elif candle.high >= position.stop:
                exit_price, reason = position.stop, "stop"
            elif candle.low <= position.target:
                exit_price, reason = position.target, "target"
        if exit_price is None:
            return
        slipped = exit_price * (
            Decimal("1") - self._config.slippage_rate
            if position.direction == "long"
            else Decimal("1") + self._config.slippage_rate
        )
        self._close(position, candle.timestamp, slipped, reason)
        self._position = None
        self._record(candle.timestamp, "exit", reason)

    def _close(self, position: PaperPosition, timestamp: int, slipped_exit: Decimal, reason: str) -> None:
        gross = ((slipped_exit - position.entry) * position.quantity
                 if position.direction == "long"
                 else (position.entry - slipped_exit) * position.quantity)
        exit_fee = slipped_exit * position.quantity * self._config.fee_rate
        fees = position.entry_fee + exit_fee
        net = gross - fees
        # Entry fee was settled at fill; realize gross minus exit fee now.
        self._cash += gross - exit_fee
        self._total_fees += exit_fee
        self._trades.append(ClosedTrade(
            position.direction, position.signal_timestamp, position.entry_timestamp,
            timestamp, position.entry, slipped_exit, position.quantity,
            gross, fees, net, reason,
        ))

    # --- persistence (restart safety) ---------------------------------------
    def snapshot(self) -> dict:
        """Serialize state to a JSON-safe mapping (decimals as strings)."""
        position = self._position
        pending = self._pending
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "config": {
                "initial_cash": str(self._config.initial_cash),
                "fee_rate": str(self._config.fee_rate),
                "slippage_rate": str(self._config.slippage_rate),
                "max_notional_fraction": str(self._config.max_notional_fraction),
            },
            "cash": str(self._cash),
            "position": None if position is None else {
                "direction": position.direction,
                "market_type": position.market_type,
                "signal_timestamp": position.signal_timestamp,
                "quantity": str(position.quantity),
                "entry": str(position.entry),
                "stop": str(position.stop),
                "target": str(position.target),
                "entry_timestamp": position.entry_timestamp,
                "entry_fee": str(position.entry_fee),
            },
            "pending": None if pending is None else {
                "signal_timestamp": pending.signal_timestamp,
                "direction": pending.direction,
                "market_type": pending.market_type,
                "quantity": str(pending.quantity),
                "stop_distance": str(pending.stop_distance),
                "target_distance": str(pending.target_distance),
            },
            "halt_reason": self._halt_reason,
            "total_fees": str(self._total_fees),
            "step_index": self._step_index,
            "last_timestamp": self._last_timestamp,
            "trades": [
                {"direction": t.direction, "signal_timestamp": t.signal_timestamp,
                 "entry_timestamp": t.entry_timestamp, "exit_timestamp": t.exit_timestamp,
                 "entry_price": str(t.entry_price), "exit_price": str(t.exit_price),
                 "quantity": str(t.quantity), "gross_pnl": str(t.gross_pnl),
                 "fees": str(t.fees), "net_pnl": str(t.net_pnl), "exit_reason": t.exit_reason}
                for t in self._trades
            ],
            "decisions": [[d.index, d.candle_timestamp, d.action, d.reason] for d in self._decisions],
        }

    @classmethod
    def restore(cls, snapshot: dict) -> "PaperEngine":
        """Rebuild an engine from snapshot(); invalid snapshots fail closed."""
        if not isinstance(snapshot, dict):
            raise TypeError("snapshot must be the mapping produced by snapshot()")
        # Schema-gated persistence: snapshots from a newer code version are
        # never silently accepted (they could mis-read state after upgrades);
        # version 1 = the pre-versioned format, treated as current here.
        version = snapshot.get("schema_version", 1)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("snapshot schema_version must be a positive integer")
        if version > SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(f"snapshot schema_version {version} is newer than supported {SNAPSHOT_SCHEMA_VERSION}")
        try:
            config_raw = snapshot["config"]
            engine = cls(PaperConfig(
                initial_cash=config_raw["initial_cash"],
                fee_rate=config_raw["fee_rate"],
                slippage_rate=config_raw["slippage_rate"],
                max_notional_fraction=config_raw["max_notional_fraction"],
            ))
            engine._cash = _d(snapshot["cash"], "cash")
            engine._halt_reason = snapshot["halt_reason"]
            engine._total_fees = _d(snapshot["total_fees"], "total_fees")
            engine._step_index = int(snapshot["step_index"])
            engine._last_timestamp = snapshot["last_timestamp"]
            raw_position = snapshot["position"]
            if raw_position is not None:
                engine._position = PaperPosition(
                    str(raw_position["direction"]), str(raw_position["market_type"]),
                    int(raw_position["signal_timestamp"]),
                    _d(raw_position["quantity"], "quantity"),
                    _d(raw_position["entry"], "entry"),
                    _d(raw_position["stop"], "stop"),
                    _d(raw_position["target"], "target"),
                    int(raw_position["entry_timestamp"]),
                    _d(raw_position["entry_fee"], "entry_fee"),
                )
            raw_pending = snapshot["pending"]
            if raw_pending is not None:
                engine._pending = TradeIntent(
                    int(raw_pending["signal_timestamp"]), str(raw_pending["direction"]),
                    _d(raw_pending["quantity"], "quantity"),
                    _d(raw_pending["stop_distance"], "stop_distance"),
                    _d(raw_pending["target_distance"], "target_distance"),
                    market_type=str(raw_pending["market_type"]),
                )
            engine._trades = [ClosedTrade(
                str(t["direction"]), int(t["signal_timestamp"]), int(t["entry_timestamp"]),
                int(t["exit_timestamp"]), _d(t["entry_price"], "entry_price"),
                _d(t["exit_price"], "exit_price"), _d(t["quantity"], "quantity"),
                _d(t["gross_pnl"], "gross_pnl"), _d(t["fees"], "fees"),
                _d(t["net_pnl"], "net_pnl"), str(t["exit_reason"]),
            ) for t in snapshot["trades"]]
            engine._decisions = [Decision(int(d[0]), int(d[1]), str(d[2]), str(d[3]))
                                 for d in snapshot["decisions"]]
        except KeyError as exc:
            raise ValueError(f"snapshot is missing required field {exc}") from None
        return engine
