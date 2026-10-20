from __future__ import annotations

from app.paper.store import SQLiteIdempotencyStore


def test_idempotency_store_survives_reopen_and_returns_original_result(tmp_path):
    path = tmp_path / "paper.sqlite3"
    first = SQLiteIdempotencyStore(path)
    assert first.get_or_reserve("key-123456", "fingerprint-a") == ("new", None)
    first.complete("key-123456", "fingerprint-a", {"net_pnl": "12.50"})

    reopened = SQLiteIdempotencyStore(path)
    assert reopened.get_or_reserve("key-123456", "fingerprint-a") == (
        "replay", {"net_pnl": "12.50"}
    )


def test_idempotency_store_rejects_reusing_key_for_different_payload(tmp_path):
    store = SQLiteIdempotencyStore(tmp_path / "paper.sqlite3")
    assert store.get_or_reserve("key-123456", "fingerprint-a") == ("new", None)
    store.complete("key-123456", "fingerprint-a", {"net_pnl": "1"})
    assert store.get_or_reserve("key-123456", "fingerprint-b") == ("conflict", None)


def test_idempotency_store_does_not_replay_incomplete_reservation(tmp_path):
    store = SQLiteIdempotencyStore(tmp_path / "paper.sqlite3")
    assert store.get_or_reserve("key-123456", "fingerprint-a") == ("new", None)
    assert store.get_or_reserve("key-123456", "fingerprint-a") == ("in_progress", None)
