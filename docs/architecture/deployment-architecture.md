# RoninTrBot — Deployment Architecture Decision (Phase 1)

**Status:** DECIDED (evidence-based, 2026-10-14) · **Scope guard:** paper/simulation only; live trading disabled in all options.

## Options evaluated

### A — JS/TS Worker (Telegram edge) + Python Core behind a versioned HTTPS API
- ✅ Clean separation; worker stays thin; Python runs its real dependencies; API contract `[/api/v1]` testable independently; matches the mission's security model (service-to-service auth, request IDs, idempotency).
- ❌ **Requires an external Python runtime (host) that does not exist today** — a paid service (VPS/PaaS). Rule 13: no paid service without explicit approval. No WebSocket/background loop can live in the worker itself regardless; it must live at the host.
- **Verdict:** TARGET ARCHITECTURE. Blocked on: hosting choice + cost approval (owner decision).

### B — Python Core inside Cloudflare Python Workers
- ❌ Evidence-based rejection for this project:
  - Python Workers run on Pyodide: only packages with pure-Python/pyodide wheels; native/binary deps unavailable; the project deliberately avoids exotic deps (good), but…
  - Request CPU budgets (ms-scale) are incompatible with the project's real workloads: walk-forward grids over ~1,200 candles × 12–16 combos × sequential backtests take seconds-to-minutes, not milliseconds.
  - Persistent paper-session loop needs Durable Objects/queues + alarms semantics — a rewrite of a `PaperEngine` that is already correct and tested, for zero correctness gain.
  - Persistent state model differs (KV/DO), forcing a second persistence re-implementation and migration risk.
- **Verdict:** NOT viable for the engine; acceptable later only for thin utility endpoints (out of scope now).

### C — Gradual hybrid (CURRENT, continuing)
- Worker (JS) = Telegram edge + **CI-parity-verified port of the analysis surface** (already landed, PR #20): read-only live market/analytics, webhook-authenticated.
- Python core = engine of record in this repo (policy/risk/paper/validation), executed where runs actually happen (CI, research scripts); no fake hosting claims.
- ✅ Zero new cost; every component already verified; nothing rewritten.
- ❌ Engine-facing pages in the bot remain display-standby until Option A's host exists — a **declared limitation**, never silently faked.
- **Verdict:** **SELECTED for the near term**, with Option A as the upgrade path once a host is approved.

## Decision

**Adopt C now; design the Option-A API contract next (design costs $0); adopt A when hosting is approved.** Do NOT force B.

## Consequences
1. `workers/ronin-panel/` holds the exact deployed worker (`wrangler.jsonc` added with it in the CI/CD phase; manual `source: api` deploys replaced by CI once secrets exist).
2. Any future `/api/v1` contract (health/status/market/strategy/paper/reports) is designed in `docs/api/openapi.yaml` BEFORE a runtime exists — no contract drift when the host lands.
3. Bot pages that need the engine show `engine: offline` honestly (already the case in v2.5.0's status payload).
4. Secrets split (worker edge vs service-to-service `INTERNAL_API_SECRET`) lands only with Option A; today's `WEBHOOK_SECRET`/`TELEGRAM_BOT_TOKEN` stay edge-scoped.

## Revisit triggers
- Host approved → execute Option A plan (contract already designed; smoke/rollback workflows already in repo by then).
- Workloads shrink to per-request CPU budgets → reconsider B for thin endpoints only.
- Cloudflare pricing/feature changes → re-evaluate costs with owner before any paid tier.
