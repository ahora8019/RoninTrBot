from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.paper.d1_store import D1IdempotencyStore


class FakeStatement:
    def __init__(self, db, sql):
        self.db, self.sql, self.values = db, " ".join(sql.split()), ()

    def bind(self, *values):
        self.values = values
        return self

    async def first(self, column=None):
        row = self.db.rows.get(self.values[0])
        return dict(row) if row is not None and self.sql.startswith("SELECT") else None

    async def run(self):
        sql, values, rows = self.sql, self.values, self.db.rows
        changes = 0
        if sql.startswith("CREATE TABLE"):
            pass
        elif sql.startswith("INSERT OR IGNORE"):
            key, fingerprint, created, updated = values
            if key not in rows:
                rows[key] = {"fingerprint": fingerprint, "state": "in_progress",
                             "result_json": None, "created_at": created, "updated_at": updated}
                changes = 1
        elif sql.startswith("UPDATE paper_idempotency SET updated_at"):
            now, key, fingerprint, cutoff = values
            row = rows.get(key)
            changes = int(bool(row and row["fingerprint"] == fingerprint
                               and row["state"] == "in_progress" and row["updated_at"] < cutoff))
            if changes:
                row["updated_at"] = now
        elif sql.startswith("UPDATE paper_idempotency SET state"):
            encoded, updated, key, fingerprint = values
            row = rows.get(key)
            changes = int(bool(row and row["fingerprint"] == fingerprint and row["state"] == "in_progress"))
            if changes:
                row.update(state="completed", result_json=encoded, updated_at=updated)
        elif sql.startswith("DELETE FROM paper_idempotency"):
            key, fingerprint = values
            row = rows.get(key)
            changes = int(bool(row and row["fingerprint"] == fingerprint and row["state"] == "in_progress"))
            if changes:
                del rows[key]
        else:
            raise AssertionError(f"Unexpected SQL in fake D1: {sql}")
        return SimpleNamespace(meta=SimpleNamespace(changes=changes))


class FakeD1:
    def __init__(self):
        self.rows = {}

    def prepare(self, sql):
        return FakeStatement(self, sql)


def test_d1_store_reserve_complete_and_replay():
    async def scenario():
        store = D1IdempotencyStore(FakeD1(), clock=lambda: 1000)
        await store.initialize()
        assert await store.get_or_reserve("key-123456", "fp-a") == ("new", None)
        assert await store.get_or_reserve("key-123456", "fp-a") == ("in_progress", None)
        result = {"net_pnl": "1.25"}
        await store.complete("key-123456", "fp-a", result)
        assert await store.get_or_reserve("key-123456", "fp-a") == ("replay", result)
    asyncio.run(scenario())


def test_d1_store_conflict_and_release():
    async def scenario():
        store = D1IdempotencyStore(FakeD1(), clock=lambda: 1000)
        assert await store.get_or_reserve("key-123456", "fp-a") == ("new", None)
        assert await store.get_or_reserve("key-123456", "fp-b") == ("conflict", None)
        await store.release("key-123456", "fp-a")
        assert await store.get_or_reserve("key-123456", "fp-b") == ("new", None)
    asyncio.run(scenario())


def test_d1_store_reclaims_only_expired_reservations():
    async def scenario():
        db, now = FakeD1(), [1000]
        store = D1IdempotencyStore(db, lease_seconds=60, clock=lambda: now[0])
        assert await store.get_or_reserve("key-123456", "fp-a") == ("new", None)
        now[0] = 1061
        assert await store.get_or_reserve("key-123456", "fp-a") == ("new", None)
        assert await store.get_or_reserve("key-123456", "fp-a") == ("in_progress", None)
    asyncio.run(scenario())


def test_d1_store_refuses_to_complete_missing_reservation():
    async def scenario():
        store = D1IdempotencyStore(FakeD1(), clock=lambda: 1000)
        with pytest.raises(RuntimeError):
            await store.complete("missing-key", "fp-a", {"ok": True})
    asyncio.run(scenario())
