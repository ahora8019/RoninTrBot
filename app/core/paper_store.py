"""SQLite persistence for deterministic PaperBroker state.

This store is deliberately opt-in. It never connects to an exchange or places
orders. Writes are transactional so the balance, open trade, and trade history
are restored as one consistent snapshot.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
import json
import math
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.paper_trading import PaperTrade, PaperTradeStatus

if TYPE_CHECKING:
    from app.core.paper_trading import PaperBroker


class SQLitePaperStore:
    """Persist and restore a PaperBroker snapshot using the stdlib SQLite driver."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS paper_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    balance REAL NOT NULL,
                    next_id INTEGER NOT NULL CHECK (next_id > 0)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS paper_trades (
                    trade_id INTEGER PRIMARY KEY,
                    payload TEXT NOT NULL
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10.0)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def save(self, broker: PaperBroker) -> None:
        """Atomically replace the persisted snapshot with the broker's current state."""
        if not math.isfinite(broker.balance) or broker.balance < 0:
            raise ValueError("broker_balance_must_be_finite_and_non_negative")
        payloads = []
        for trade in broker.trades:
            payload = asdict(trade)
            payload["status"] = trade.status.value
            payloads.append((trade.trade_id, json.dumps(payload, allow_nan=False, separators=(",", ":"))))
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO paper_state(singleton, balance, next_id) VALUES (1, ?, ?)
                   ON CONFLICT(singleton) DO UPDATE SET balance=excluded.balance, next_id=excluded.next_id""",
                (broker.balance, broker._next_id),
            )
            connection.execute("DELETE FROM paper_trades")
            connection.executemany(
                "INSERT INTO paper_trades(trade_id, payload) VALUES (?, ?)", payloads
            )

    def restore(self, broker: PaperBroker) -> bool:
        """Restore a snapshot into a fresh broker; return False if no snapshot exists.

        The caller must construct the broker with the same policy used by the
        saved run. Existing broker activity is rejected to avoid accidental
        overwrites or merging histories.
        """
        if broker.trades or broker.open_trade is not None:
            raise ValueError("restore_requires_fresh_broker")
        with self._connect() as connection:
            state = connection.execute(
                "SELECT balance, next_id FROM paper_state WHERE singleton=1"
            ).fetchone()
            if state is None:
                return False
            rows = connection.execute(
                "SELECT payload FROM paper_trades ORDER BY trade_id"
            ).fetchall()

        balance, next_id = float(state[0]), int(state[1])
        if not math.isfinite(balance) or balance < 0 or next_id < 1:
            raise ValueError("persisted_paper_state_invalid")
        trades: list[PaperTrade] = []
        for (raw_payload,) in rows:
            payload = json.loads(raw_payload)
            payload["status"] = PaperTradeStatus(payload["status"])
            trade = PaperTrade(**payload)
            if trade.trade_id < 1 or not math.isfinite(trade.net_pnl):
                raise ValueError("persisted_paper_trade_invalid")
            trades.append(trade)

        open_trades = [trade for trade in trades if trade.status is PaperTradeStatus.OPEN]
        if len(open_trades) > 1:
            raise ValueError("persisted_state_has_multiple_open_trades")
        if any(trade.trade_id >= next_id for trade in trades):
            raise ValueError("persisted_next_id_is_not_monotonic")

        broker.balance = balance
        broker._next_id = next_id
        broker.trades = trades
        broker._open = open_trades[0] if open_trades else None
        return True
