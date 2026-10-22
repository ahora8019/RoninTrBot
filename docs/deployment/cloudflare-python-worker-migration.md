# Cloudflare Python Worker migration gate

Status: **BLOCKED — implementation must not be deployed yet**

This document records the compatibility audit for moving the Python Core onto
Cloudflare Workers. It is intentionally conservative: a successful import or a
health endpoint is not proof that market data and Paper Trading work correctly.

## Current blockers found in the repository

1. `app/internal_api.py` is a WSGI application and imports
   `wsgiref.simple_server.make_server`. Cloudflare Workers do not run a
   persistent WSGI server; routing must be adapted to the Python Worker request
   entrypoint without changing the existing JSON envelope or auth behavior.
2. `app/market_data/lbank.py` uses `urllib.request`, `urlopen`,
   `threading.Lock`, `time.sleep`, and a synchronous retry loop. This path
   must be replaced or adapted to the Workers fetch/runtime model and bounded
   asynchronous retries. Do not assume that a normal CPython transport works
   under Pyodide.
3. `app/paper/store.py` depends on `sqlite3` and a local filesystem path.
   A Worker-local file is not a durable database. The store contract must become
   asynchronous and use D1 (or another explicitly selected durable binding).
   D1 reservation and completion operations must be atomic enough to preserve
   the existing idempotency guarantees under concurrent requests.
4. The Paper API currently creates its store and process-local status counters
   inside `create_app()`. Worker isolates can be restarted or run concurrently;
   process-local counters cannot be presented as durable operational state.
5. The current Telegram Worker integration expects an authenticated HTTPS API
   with the existing `{ ok, request_id, data, error }` envelope. Keep that
   contract stable, reject partial configuration, and never silently fall back
   to a second strategy implementation when the Core is configured but fails.

## Required migration sequence

- [ ] Add a Cloudflare Python Worker entrypoint and `pywrangler` configuration
      on a non-production branch. Start with authenticated health/status only.
- [ ] Add a Workers-compatible asynchronous LBank transport with bounded
      timeout/retry behavior and injected fake transport tests. Preserve strict
      candle validation and public read-only scope.
- [ ] Define an async idempotency-store protocol and a D1 implementation.
      Test new reservation, replay, fingerprint conflict, active reservation,
      abandoned reservation recovery, and concurrent duplicate requests.
- [ ] Adapt paper-session orchestration to await the durable store. Do not use
      local SQLite as a Cloudflare fallback.
- [ ] Add a CI job that installs/checks the Python Worker toolchain and runs
      local Worker tests. A standard `pytest` pass alone is insufficient.
- [ ] Add a separate preview-only deployment path and smoke test all routes
      with invalid auth, missing config, malformed input, upstream failure,
      and D1 failure cases.
- [ ] Only after the above gates pass, connect the Telegram Worker through a
      same-account Service Binding where possible; otherwise require HTTPS and
      a secret stored outside source control.
- [ ] Production deployment remains a separate, manually triggered action.

## Acceptance criteria

- No private LBank endpoints, account keys, order placement, withdrawals, or
  live trading are introduced.
- All API routes retain the existing response envelope and fail-closed auth.
- Paper idempotency survives isolate restarts and concurrent requests.
- The Telegram Worker never treats a Core error as permission to use a
  different strategy engine.
- A live `workers.dev` smoke test is marked **NOT RUN** until a preview
  deployment is deliberately created; never report it as passed based on
  static checks.

## Why not just copy the Python files?

The existing implementation relies on CPython server, networking, threading,
and SQLite behavior. Cloudflare Python Workers run Python in a constrained
Pyodide/WebAssembly runtime. A superficial copy could make the health route
appear to work while market-data or Paper Trading routes fail at runtime.
