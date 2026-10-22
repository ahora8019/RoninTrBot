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


def test_idempotency_store_reclaims_abandoned_reservation_after_lease_timeout(tmp_path):
    import sqlite3

    path = tmp_path / "paper.sqlite3"
    store = SQLiteIdempotencyStore(path)
    assert store.get_or_reserve("key-123456", "fingerprint-a") == ("new", None)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE paper_idempotency SET updated_at = unixepoch() - 3601 WHERE idempotency_key = ?",
            ("key-123456",),
        )

    assert store.get_or_reserve("key-123456", "fingerprint-a") == ("new", None)
    assert store.get_or_reserve("key-123456", "fingerprint-a") == ("in_progress", None)
