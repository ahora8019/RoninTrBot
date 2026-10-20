"""Durable idempotency records for lab-only paper session requests.

The store persists request fingerprints and completed JSON results in SQLite.
It never stores exchange credentials or submits orders.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class SQLiteIdempotencyStore:
    """SQLite-backed idempotency ledger with atomic key reservation."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('in_progress', 'completed')),
                    result_json TEXT,
                    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
                    updated_at INTEGER NOT NULL DEFAULT (unixepoch())
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level="IMMEDIATE")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def get_or_reserve(self, key: str, fingerprint: str) -> tuple[str, dict[str, Any] | None]:
        """Return new, replay, conflict, or in_progress; reservation is atomic."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT fingerprint, state, result_json FROM paper_idempotency WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO paper_idempotency (idempotency_key, fingerprint, state) VALUES (?, ?, 'in_progress')",
                    (key, fingerprint),
                )
                return "new", None
            old_fingerprint, state, result_json = row
            if old_fingerprint != fingerprint:
                return "conflict", None
            if state == "in_progress":
                return "in_progress", None
            return "replay", json.loads(result_json)

    def complete(self, key: str, fingerprint: str, result: dict[str, Any]) -> None:
        encoded = json.dumps(result, separators=(",", ":"), sort_keys=True)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE paper_idempotency
                SET state = 'completed', result_json = ?, updated_at = unixepoch()
                WHERE idempotency_key = ? AND fingerprint = ? AND state = 'in_progress'
                """,
                (encoded, key, fingerprint),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("paper idempotency reservation is missing or no longer active")
