# Rollback & Recovery (ronin-panel)

- Every deployment records: git SHA (CI variable), Cloudflare deployment ID/version (wrangler output), and the smoke-test result.
- Rollback (worker code): `npx wrangler deployments list` then `npx wrangler rollback --config workers/ronin-panel/wrangler.jsonc`, then re-run `scripts/smoke_test.py` and record the outcome.
- Rollback NEVER touches data (KV) — a bad broadcast list is a data incident, follow docs/runbooks/recovery.md instead.
- Rollback NEVER silently changes; every rollback gets a dated audit entry (who/why/SHA/results).
- If rollback itself fails: stop; fetch current live state (`GET /` + webhook-info), and escalate per incident-response runbook — no force changes, no deletions.
