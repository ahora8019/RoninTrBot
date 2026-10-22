"""Cloudflare D1-backed async idempotency store.

The adapter is independent of the Worker entrypoint so its state machine can
be tested with a fake D1 binding. All database calls are asynchronous.
"""
from __future__ import annotations

import json
import time
from typing import Any, Protocol


class D1Statement(Protocol):
    def bind(self, *values: Any) -> "D1Statement": ...
    async def first(self, column: str | None = None) -> Any: ...
    async def run(self) -> Any: ...


class D1Database(Protocol):
    def prepare(self, query: str) -> D1Statement: ...


def _changes(result: Any) -> int | None:
    meta = getattr(result, "meta", None)
    value = getattr(meta, "changes", None) if meta is not None else None
    return value if isinstance(value, int) else None


class D1IdempotencyStore:
    """D1 idempotency ledger; database primary-key uniqueness arbitrates races."""

    def __init__(self, db: D1Database, *, lease_seconds: int = 3600,
                 clock=time.time) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self._db = db
        self._lease_seconds = lease_seconds
        self._clock = clock

    async def initialize(self) -> None:
        await self._db.prepare(
            """CREATE TABLE IF NOT EXISTS paper_idempotency (
                idempotency_key TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('in_progress', 'completed')),
                result_json TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        ).run()

    async def get_or_reserve(self, key: str, fingerprint: str) -> tuple[str, dict[str, Any] | None]:
        now = int(self._clock())
        row = await self._db.prepare(
            "SELECT fingerprint, state, result_json, updated_at "
            "FROM paper_idempotency WHERE idempotency_key = ?"
        ).bind(key).first()
        if row is not None:
            if row["fingerprint"] != fingerprint:
                return "conflict", None
            if row["state"] == "completed":
                if not row["result_json"]:
                    raise RuntimeError("completed idempotency record has no result")
                return "replay", json.loads(row["result_json"])
            if int(row["updated_at"]) >= now - self._lease_seconds:
                return "in_progress", None
            reclaimed = await self._db.prepare(
                "UPDATE paper_idempotency SET updated_at = ? "
                "WHERE idempotency_key = ? AND fingerprint = ? "
                "AND state = 'in_progress' AND updated_at < ?"
            ).bind(now, key, fingerprint, now - self._lease_seconds).run()
            return ("new", None) if _changes(reclaimed) == 1 else ("in_progress", None)

        inserted = await self._db.prepare(
            "INSERT OR IGNORE INTO paper_idempotency "
            "(idempotency_key, fingerprint, state, result_json, created_at, updated_at) "
            "VALUES (?, ?, 'in_progress', NULL, ?, ?)"
        ).bind(key, fingerprint, now, now).run()
        if _changes(inserted) == 1:
            return "new", None

        row = await self._db.prepare(
            "SELECT fingerprint, state, result_json FROM paper_idempotency "
            "WHERE idempotency_key = ?"
        ).bind(key).first()
        if row is None:
            raise RuntimeError("D1 reservation could not be read back")
        if row["fingerprint"] != fingerprint:
            return "conflict", None
        if row["state"] == "completed":
            if not row["result_json"]:
                raise RuntimeError("completed idempotency record has no result")
            return "replay", json.loads(row["result_json"])
        return "in_progress", None

    async def release(self, key: str, fingerprint: str) -> None:
        await self._db.prepare(
            "DELETE FROM paper_idempotency WHERE idempotency_key = ? "
            "AND fingerprint = ? AND state = 'in_progress'"
        ).bind(key, fingerprint).run()

    async def complete(self, key: str, fingerprint: str, result: dict[str, Any]) -> None:
        encoded = json.dumps(result, separators=(",", ":"), sort_keys=True)
        updated = await self._db.prepare(
            "UPDATE paper_idempotency SET state = 'completed', result_json = ?, updated_at = ? "
            "WHERE idempotency_key = ? AND fingerprint = ? AND state = 'in_progress'"
        ).bind(encoded, int(self._clock()), key, fingerprint).run()
        if _changes(updated) != 1:
            raise RuntimeError("paper idempotency reservation is missing or no longer active")
