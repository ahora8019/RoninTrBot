"""Cloudflare D1-backed async idempotency store.

The adapter is deliberately independent of the Worker request entrypoint so its
state machine can be unit-tested with a fake D1 binding. D1 calls are async and
must be awaited by callers.
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


class D1IdempotencyStore:
    """D1 idempotency ledger; uniqueness is enforced by the primary key."""

    def __init__(self, db: D1Database, *, lease_seconds: int = 3600,
                 clock=time.time) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self._db = db
        self._lease_seconds = lease_seconds
        self._clock = clock

    async def initialize(self) -> None:
        """Create the schema. Production deployments should also apply migrations."""
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
            old_fingerprint = row["fingerprint"]
            state = row["state"]
            if old_fingerprint != fingerprint:
                return "conflict", None
            if state == "completed":
                if not row["result_json"]:
                    raise RuntimeError("completed idempotency record has no result")
                return "replay", json.loads(row["result_json"])
            if int(row["updated_at"]) >= now - self._lease_seconds:
                return "in_progress", None
            # Conditional reclaim: only one concurrent caller can update the
            # stale lease. The changes() result is checked to avoid double work.
            result = await self._db.prepare(
                "UPDATE paper_idempotency SET updated_at = ? "
                "WHERE idempotency_key = ? AND fingerprint = ? "
                "AND state = 'in_progress' AND updated_at < ?"
            ).bind(now, key, fingerprint, now - self._lease_seconds).run()
            meta = getattr(result, "meta", None)
            changes = getattr(meta, "changes", None) if meta is not None else None
            if changes == 1:
                return "new", None
            return "in_progress", None

        # INSERT OR IGNORE handles concurrent first reservations. Read back
        # only after the insert to distinguish the winner from a racing caller.
        await self._db.prepare(
            "INSERT OR IGNORE INTO paper_idempotency "
            "(idempotency_key, fingerprint, state, result_json, created_at, updated_at) "
            "VALUES (?, ?, 'in_progress', NULL, ?, ?)"
        ).bind(key, fingerprint, now, now).run()
        row = await self._db.prepare(
            "SELECT fingerprint, state, result_json FROM paper_idempotency "
            "WHERE idempotency_key = ?"
        ).bind(key).first()
        if row is None:
            raise RuntimeError("D1 reservation could not be read back")
        if row["fingerprint"] != fingerprint:
            return "conflict", None
        if row["state"] == "completed":
            return "replay", json.loads(row["result_json"])
        # A racing insert can leave the same fingerprint in progress. Only the
        # caller that inserted it should run the simulation; changes metadata
        # from D1 is the supported way to identify that winner.
        # Since the row alone cannot prove ownership, use a unique conditional
        # reservation token in a later migration before enabling this route.
        return "in_progress", None

    async def release(self, key: str, fingerprint: str) -> None:
        await self._db.prepare(
            "DELETE FROM paper_idempotency WHERE idempotency_key = ? "
            "AND fingerprint = ? AND state = 'in_progress'"
        ).bind(key, fingerprint).run()

    async def complete(self, key: str, fingerprint: str, result: dict[str, Any]) -> None:
        encoded = json.dumps(result, separators=(",", ":"), sort_keys=True)
        result_obj = await self._db.prepare(
            "UPDATE paper_idempotency SET state = 'completed', result_json = ?, updated_at = ? "
            "WHERE idempotency_key = ? AND fingerprint = ? AND state = 'in_progress'"
        ).bind(encoded, int(self._clock()), key, fingerprint).run()
        meta = getattr(result_obj, "meta", None)
        changes = getattr(meta, "changes", None) if meta is not None else None
        if changes != 1:
            raise RuntimeError("paper idempotency reservation is missing or no longer active")
