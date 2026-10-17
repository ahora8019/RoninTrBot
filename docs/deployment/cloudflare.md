# Cloudflare Deployment (RoninTrBot)

The repository deployment target is the Project Cloudflare account's Worker
at https://ronintrbot.ahora-project.workers.dev. Preview URLs remain disabled.

## Deploy path
1. CI (ci.yml) must pass Python tests including Python-JavaScript parity, worker syntax/config checks, and secret-pattern scan.
2. deploy-worker.yml runs manually or after a push to main that changes workers/** or the deployment workflow.
3. Deployment requires GitHub Actions secrets CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID. Missing credentials block deployment.
4. Wrangler deploys with --keep-vars to preserve dashboard-managed role variables.
5. The post-deploy smoke test checks the health payload and fails on invalid responses.

## Cloud resources
- KV binding USERS_KV: namespace ID 97fdd4c0960e4b60bedee671172b52b7.
- Worker secrets TELEGRAM_BOT_TOKEN and WEBHOOK_SECRET must be configured directly in Cloudflare. WEBHOOK_SECRET is mandatory: Telegram's webhook must be registered with the matching secret_token. The deployment smoke test fails unless health reports webhookAuth=secret_verified. Never commit secrets or paste them into chat.
- Dashboard variables OWNER_TELEGRAM_ID and ADMIN_TELEGRAM_IDS are environment-specific.
- This configuration alone does not prove Telegram or exchange connectivity.

## Safety
No paid resource should be enabled without explicit approval. Live order placement remains out of scope; trading stays read-only/paper-only.

## Smoke test
The smoke test uses an explicit user agent to reduce false positives from Cloudflare edge bot protection. HTTP errors, network failures, malformed JSON, invalid health payloads, offline status, or an unverified webhook secret fail the smoke test.
