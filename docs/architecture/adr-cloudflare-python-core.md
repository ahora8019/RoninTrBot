# ADR: Host the read-only Python Core on Cloudflare Workers

- Status: Proposed; no deployment or Cloudflare account changes authorized by this document.
- Date: 2026-10-23
- Scope: First hosted slice for market data and strategy analysis only.

## Decision

Prototype the read-only Python Core as a separate **Python Worker** in the existing Cloudflare project, while keeping the Telegram panel in its current JavaScript Worker. Use a Worker-to-Worker Service Binding where practical, or a private HTTPS endpoint with bearer authentication if the deployment topology requires it. Prefer the Service Binding because it avoids exposing the internal API as a public network endpoint and avoids sending a shared bearer secret over an external hop.

Keep Python as the source of truth for market-data normalization and strategy analysis. Do not port strategy logic into JavaScript.

## Why this is the preferred first step

- It avoids introducing a separate paid host before we have proven the end-to-end integration.
- Cloudflare officially supports Python Workers and WSGI/ASGI adapters; Python Workers can use Worker bindings and secrets.
- The current API module is **not** a drop-in Cloudflare Worker. It imports `wsgiref.simple_server`, `urllib.request`, `threading`, and `sqlite3`-backed storage. These assumptions need to be separated from the portable strategy code before deployment.
- The existing public LBank adapter uses synchronous `urllib` network calls and a thread lock for rate limiting. For the Worker prototype, implement an asynchronous fetch transport compatible with the Workers runtime and retain strict response validation, timeouts, bounded retries, and rate controls.
- The strategy module is deterministic and has no network or credential dependency, making it a good first module to reuse directly.

## Phase 1: read-only endpoints

1. Add a dedicated Python Worker entrypoint and Wrangler config; do not change the existing production Worker config.
2. Expose only authenticated/internal health, public LBank Spot OHLCV, and Python-owned strategy analysis.
3. Validate symbols, intervals (15m/1h/4h), and candle limits; reject malformed or stale market data; return no-store JSON responses.
4. Prefer a Service Binding from the Telegram Worker to the Python Worker. If unsupported by the current deployment layout, use HTTPS plus the existing bearer-secret contract, with redirects rejected and secrets never logged.
5. Add unit/contract tests for route validation, auth, error handling, data validation, and Python/Worker response parity.
6. Verify in local CI first. A live integration test is a separate gated step and must not deploy Production automatically.

## Phase 2: paper-session persistence

The current paper endpoint uses SQLite file persistence. Do **not** deploy that endpoint unchanged to Workers: a normal Worker filesystem is not a durable SQLite volume. Keep the paper endpoint disabled until its idempotency ledger and results are migrated to a Cloudflare D1 database (or another explicitly approved durable store), with atomic key reservation, conflict handling, replay behavior, and recovery tests.

## Safety and exclusions

- No exchange account endpoints, private LBank credentials, order placement, withdrawals, or live trading.
- No Production deployment, Cloudflare resource creation, secret changes, or paid resources as part of this ADR.
- Do not enable Paper Trading on the hosted Worker until persistence and recovery semantics pass tests.
- Keep the existing standalone WSGI API available for local tests or a separately approved host; do not silently remove it.
- Any Worker-to-Worker service binding or deployment change must be introduced as a reviewed PR and tested before explicit Production approval.

## Acceptance criteria

- [ ] Python Worker imports only runtime-compatible modules.
- [ ] Public LBank OHLCV fetch uses an async Workers-compatible transport.
- [ ] Health and strategy endpoints are protected according to the chosen internal routing model.
- [ ] Market responses validate schema, symbol, interval, candle ordering, freshness, and response size.
- [ ] Existing Python strategy tests remain green.
- [ ] New Worker contract tests and CI pass.
- [ ] Paper endpoint remains unavailable until D1-backed idempotency is implemented and tested.
- [ ] No Production deploy or paid resource is triggered automatically.
