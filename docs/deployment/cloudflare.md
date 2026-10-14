# Cloudflare Deployment (ronin-panel)

Environments: **local** (repo), **CI** (tests/static), **production** = the single live worker `ronin-panel` on `ronintrb.workers.dev`. Staging/preview is intentionally NOT created (see deployment-architecture.md: single small worker, previews disabled; a preview policy lands only when the service grows).

## Deploy path (as of CI wiring)
1. CI (`ci.yml`) must be green: python tests (incl. parity), worker static checks, secret-pattern scan.
2. `deploy-worker.yml` (manual or push-to-main touching `workers/**`) — gated on `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` secrets; **fails loudly when missing, never fakes**.
3. Commit SHA of the deployment is echoed in the run (traceability manual → automatic).
4. `scripts/smoke_test.py` verifies the live health payload (version semver, required fields, webhook-auth state).

## What lives in the cloud (never in repo)
Worker secrets (`TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`), vars (`OWNER_TELEGRAM_ID`, `ADMIN_TELEGRAM_IDS`), KV binding `USERS_KV` → namespace `66e90b78…`.

## Costs
Workers Free tier is sufficient at current traffic (public-data fetches on demand only). No paid resource may be enabled without explicit owner approval (rule 13).

## Known edge behavior (verified 2026-10-14)

Cloudflare's edge bot-protection blocks default `Python-urllib/x.y` user agents with HTTP 403 on `workers.dev` health GETs; explicit descriptive UAs pass. `scripts/smoke_test.py` sends `RoninTrBot-SmokeTest/1.0` for this reason and reports a 403 with diagnosis hint instead of a bare failure.
