# Recovery runbook (data & state)

Paper engine state: `PaperEngine.snapshot()` is JSON-safe with `schema_version` gating; `restore()` accepts v1 (pre-versioned) and current, rejects newer. Store snapshots only where the runtime writes (worker KV/USERS_KV today; Option-A service store later).

- **Clock drift / duplicate candles:** engines reject out-of-order timestamps (validated); never replay a snapshot twice without checking `last_timestamp`.
- **USERS_KV corruption:** list keys with prefix `user:` and `broadcast:pending:` — pending entries auto-expire (TTL 600s); never delete in bulk without owner approval.
- **Migration failure:** stop promotion; run `scripts/smoke_test.py`; record schema vs code version; restore only from a snapshot whose `schema_version` the current code ACCEPTS.
- **Rollback ≠ data rollback:** code rollback never deletes or rewrites data.
