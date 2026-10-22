from __future__ import annotations

import json
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
        key = self.values[0]
        row = self.db.rows.get(key)
        if row is None:
            return None
        if self.sql.startswith("SELECT"):
            return dict(row)
        return None

    async def run(self):
        sql, values, rows = self.sql, self.values, self.db.rows
        if sql.startswith("CREATE TABLE"):
            return SimpleNamespace(meta=SimpleNamespace(changes=0))
        if sql.startswith("INSERT OR IGNORE"):
            key, fingerprint, created, updated = values
            if key in rows:
                changes = 0
            else:
                rows[key] = {
                    "fingerprint": fingerprint, "state": "in_progress",
                    "result_json": None, "created_at": created, "updated_at": updated,
                }
                changes = 1
            return SimpleNamespace(meta=SimpleNamespace(changes=changes))
        if sql.startswith("UPDATE paper_idempotency SET updated_at"):
            now, key, fingerprint, cutoff = values
            row = rows.get(key)
            changes = int(bool(row and row["fingerprint"] == fingerprint
                               and row["state"] == "in_progress"
                               and row["updated_at"] < cutoff))
            if changes:
                row["updated_at"] = now
            return SimpleNamespace(meta=SimpleNamespace(changes=changes))
        if sql.startswith("UPDATE paper_idempotency SET state"):
            encoded, updated, key, fingerprint = values
            row = rows.get(key)
            changes = int(bool(row and row["fingerprint"] == fingerprint
                               and row["state"] == "in_progress"))
            if changes:
                row.update(state="completed", result_json=encoded, updated_at=updated)
            return SimpleNamespace(meta=SimpleNamespace(changes=changes))
        if sql.startswith("DELETE FROM paper_idempotency"):
            key, fingerprint = values
            row = rows.get(key)
            if row and row["fingerprint"] == fingerprint and row["state"] == "in_progress":
                del rows[key]
                changes = 1
            else:
                changes = 0
            return SimpleNamespace(meta=SimpleNamespace(changes=changes))
        raise AssertionError(f"Unexpected SQL in fake D1: {sql}")


class FakeD1:
    def __init__(self):
        self.rows = {}

    def prepare(self, sql):
        return FakeStatement(self, sql)


@pytest.mark.asyncio
async def test_d1_store_reserve_complete_and_replay():
    db = FakeD1()
    now = [1000]
    store = D1IdempotencyStore(db, clock=lambda: now[0])
    await store.initialize()

    assert await store.get_or_reserve("key-123456", "fp-a") == ("new", None)
    assert await store.get_or_reserve("key-123456", "fp-a") == ("in_progress", None)
    result = {"net_pnl": "1.25"}
    await store.complete("key-123456", "fp-a", result)
    assert await store.get_or_reserve("key-123456", "fp-a") == ("replay", result)


@pytest.mark.asyncio
async def test_d1_store_conflict_and_release():
    store = D1IdempotencyStore(FakeD1(), clock=lambda: 1000)
    assert await store.get_or_reserve("key-123456", "fp-a") == ("new", None)
    assert await store.get_or_reserve("key-123456", "fp-b") == ("conflict", None)
    await store.release("key-123456", "fp-a")
    assert await store.get_or_reserve("key-123456", "fp-b") == ("new", None)


@pytest.mark.asyncio
async def test_d1_store_reclaims_only_expired_reservations():
    db = FakeD1()
    now = [1000]
    store = D1IdempotencyStore(db, lease_seconds=60, clock=lambda: now[0])
    assert await store.get_or_reserve("key-123456", "fp-a") == ("new", None)
    now[0] = 1061
    assert await store.get_or_reserve("key-123456", "fp-a") == ("new", None)
    assert await store.get_or_reserve("key-123456", "fp-a") == ("in_progress", None)


@pytest.mark.asyncio
async def test_d1_store_refuses_to_complete_missing_reservation():
    store = D1IdempotencyStore(FakeD1(), clock=lambda: 1000)
    with pytest.raises(RuntimeError):
        await store.complete("missing-key", "fp-a", {"ok": True})
