# Cloudflare Deployment (RoninTrBot)

The repository deployment target is the Project Cloudflare account's Worker
at https://ronintrbot.ahora-project.workers.dev. Preview URLs remain disabled.

## Deploy path
1. CI (ci.yml) must pass Python tests including Python-JavaScript parity, worker syntax/config checks, and secret-pattern scan.
2. Production deploy-worker.yml is manual-dispatch only. Merging or pushing code must not deploy Production automatically.
3. Deployment requires GitHub Actions secrets CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID. Missing credentials block deployment.
4. Wrangler deploys with --keep-vars to preserve dashboard-managed role variables.
5. The post-deploy smoke test checks the health payload and fails on invalid responses.

## Cloud resources
- KV binding USERS_KV: namespace ID 97fdd4c0960e4b60bedee671172b52b7.
- Worker secrets TELEGRAM_BOT_TOKEN and WEBHOOK_SECRET must be configured directly in Cloudflare. WEBHOOK_SECRET is mandatory: Telegram's webhook must be registered with the matching secret_token. The deployment smoke test fails unless health reports webhookAuth=secret_verified. Never commit secrets or paste them into chat.
- Dashboard variables OWNER_TELEGRAM_ID and ADMIN_TELEGRAM_IDS are environment-specific.
- To route Telegram Market Data and Strategy analysis through Python Core, configure the Worker variable `PYTHON_CORE_URL` to the HTTPS origin/base path of the authenticated Python API (do not include credentials, query strings, or fragments) and configure the Worker secret `INTERNAL_API_SECRET` to the same bearer secret used by the Python service. Never put the secret in `wrangler.jsonc`, Git, logs, or chat.
- The Worker calls Python Core for market candles and strategy analysis only when the integration is configured. A partial configuration fails closed; with neither setting configured, the existing public-LBank development fallback remains active. A configured but unavailable Core does not silently fall back to a second strategy implementation.
- Python Core must be hosted separately on a trusted HTTPS endpoint before the Worker integration can be enabled. This repository change does not deploy Python Core or configure Cloudflare resources.
- This configuration alone does not prove Telegram or exchange connectivity.

## Safety
No paid resource should be enabled without explicit approval. Live order placement remains out of scope; trading stays read-only/paper-only.

## Smoke test
The smoke test uses an explicit user agent to reduce false positives from Cloudflare edge bot protection. It performs a read-only GET health check, then sends an empty synthetic POST without the Telegram secret header and requires HTTP 403. It never calls Telegram or triggers trading. HTTP errors, network failures, malformed JSON, invalid health payloads, offline status, missing webhook configuration, or a POST that is not forbidden fail the test.
