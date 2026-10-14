# RoninTrBot — Current State (Phase 0 Discovery)

**As of:** 2026-10-14 (UTC) · **Method:** direct verification via GitHub API, Cloudflare API, local repo inspection — no assumptions from stale documentation. **Scope guard:** live trading is disabled everywhere; paper/simulation only.

---

## 1. Repository

| Item | Verified state | Evidence |
|---|---|---|
| Default branch | `main` @ `2c45c9b` | GitHub API |
| Python suite | **188 tests green on main**; 208 on `feat/worker-js-port` | local `pytest`, CI |
| CI | single workflow `.github/workflows/tests.yml` (pytest only) | file inspection |
| GitHub Actions secrets | **NONE configured** | `GET /repos/.../actions/secrets` → 0 |
| Lockfile / dependency pinning | **none** (`pip install -e '.[test]'` unpinned) | repo root |
| `wrangler.toml` / `package.json` | **none** | repo tree |
| `docs/` | absent | repo tree |
| Remote branches | **21** (≈19 stale feature branches incl. merged ones) | `git branch -r` |
| Open PRs | **#10** (docs: news-intelligence design), **#18** (STALE duplicate of the multi-strategy stage — superseded), **#19** (multi-strategy scaffold + breakout; CI green), **#20** (worker versioned in-repo + JS port; STATCKED on #19; CI green) | GitHub API |

### Python core (all present on main, unit-tested)
- `app/core/` — config, logging; `app/main.py` — entry (Telegram polling service, paper-only).
- `app/market_data/` — LBank read-only adapter (`fetch_ohlcv`, fail-closed validation; `on_invalid="skip"` research mode), router, types.
- `app/strategy/` — indicators (SMA/RSI/ATR + scoring), `policy.py` (trend policy → risk-sized intents). **In PR #19:** `registry.py` (regime classifier + frozen router, stand-aside first-class), `breakout.py` (Donchian + volume filter).
- `app/risk/engine.py` — `build_risk_plan` (ATR stops, 1% risk budget, hard caps 2%/1.0×/3×).
- `app/paper/` — hermetic `PaperEngine` (next-open fills, stop-first, dual-side fees, decision log, halt/resume, snapshot/restore) + `runner.py` (policy→risk→paper session).
- `app/validation/` — backtest (no-lookahead marks export), `splits.py` (chronological OOS), `walkforward.py` (batch + per-step protocols, chained equity/drawdown).
- `app/telegram/` — polling handlers/auth (Python bot variant, separate from the deployed worker).
- `scripts/` — `oos_study.py`, `multi_param_study.py` (real-data, evidence documented; JSON outputs kept out of repo).

## 2. Cloudflare (account `ff120baa…`, verified 2026-10-14)

| Item | Verified state |
|---|---|
| Workers | 5 total; **only `ronin-panel` is RoninTrBot's** (others: `sakurapanel*`, two legacy) |
| `ronin-panel` | v2.5.0 live; `webhookAuth: secret_verified`; route `<subdomain>ronintrb.workers.dev`; `previews_enabled: false` |
| Deployments | source **`api`** (manual API uploads) — latest 2026-10-14T13:32; **no git-SHA linkage** |
| Secrets (names) | `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET` |
| Vars/bindings | `OWNER_TELEGRAM_ID`, `ADMIN_TELEGRAM_IDS` (empty), `USERS_KV` → namespace `66e90b78…` |
| KV namespaces | **13** in account (mostly other projects; only `USERS_KV` belongs here) |
| Wrangler/CI link | **none** — worker config exists only in the cloud |

## 3. What is functional vs. placeholder

- ✅ Functional (tested): market-data integrity pipeline, strategy policies, risk sizing as single choke point, paper engine + session runner, OOS/walk-forward evaluation with real-data evidence (multi-param + multi-strategy studies), Telegram worker (auth gate verified end-to-end; live market charts; live strategy analysis via the JS port in #20).
- 🟡 Placeholder by design (documented): engine start/stop/emergency buttons (display-only until a runtime hosts the Python core), `chartsPage` overview, settings page values (static), News Intelligence (design-only, PR #10).
- ❌ Not existing: service-to-service API contract, preview/staging environments, CI-driven deployments, persistence versioning/migrations, observability/metrics endpoints beyond worker GET.

## 4. Prioritized risks / blockers

| # | Risk | Severity | First fix |
|---|---|---|---|
| R1 | Multi-strategy + worker versioning stuck in unmerged PRs (#19 → #20), stale duplicate #18 | 🔴 | Merge #19, retarget+merge #20, close #18 (needs owner approval) |
| R2 | No GitHub Actions secrets (CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID) → CI deploy impossible | 🔴 | Create least-privilege token + repo secrets (owner action/approval) |
| R3 | Worker deploys are manual `source: api`, no git-SHA linkage, no wrangler config → not reproducible from CI | 🟠 | `wrangler.jsonc` + deploy workflow (needs R2) |
| R4 | Python deps unpinned (no lockfile) → non-reproducible CI | 🟠 | `pip-compile`/constraints + pinned CI install |
| R5 | ~19 stale remote branches | 🟠 | Cleanup after merges (approval; never auto-delete) |
| R6 | Preview policy undefined; account hosts other projects; 13 KV namespaces → resource sprawl risk | 🟡 | Preview naming/limits + cleanup policy doc |
| R7 | No docs/runbooks; README minimal | 🟡 | docs/* tree (this mission) |
| R8 | Telegram Python handlers and the JS worker are two parallel bot surfaces (no shared contract) | 🟡 | Phase-1 architecture decision, documented |

## 5. Strengths to preserve

208 functional tests incl. parity (Python↔JS); fail-closed engines; verified real-data OOS evidence; webhook auth proven end-to-end (403 on forged POST); secrets handled conversation-only throughout; every Cloudflare deployment verified before/after with bindings intact.

---
*Next phase:* `docs/architecture/deployment-architecture.md` — options A/B/C evaluated with the evidence above. Blockers requiring owner approval: R1 merges, R2 secrets.
