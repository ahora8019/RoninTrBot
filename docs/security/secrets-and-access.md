# Secrets & Access (RoninTrBot)

**Rules of record:** values are NEVER in the repo, workflows, logs, tests, issues, or reports. Only existence + scope names appear here.

## Inventory (verified 2026-10-14)

| Secret | Where it lives | Scope | Used by |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Cloudflare worker secret (ronin-panel) | Telegram Bot API, bot @RoninTRbot | worker edge only |
| `WEBHOOK_SECRET` | Cloudflare worker secret | `X-Telegram-Bot-Api-Secret-Token` verify | worker ↔ Telegram |
| `CLOUDFLARE_API_TOKEN` | GitHub Actions secret **(NOT yet configured — deploy workflow fails loudly without it)** | least privilege: `Workers Scripts: Edit` on this account only | deploy CI |
| `CLOUDFLARE_ACCOUNT_ID` | GitHub Actions secret (not secret-sensitive but kept together) | account scoping | deploy CI |
| `INTERNAL_API_SECRET` | future (Option A) | service-to-service bearer, per-environment | Python core ↔ edge |

## Access rules
- Least privilege everywhere; edge secrets never leave the edge; service secrets never reach the edge.
- Preview/CI never sees production values; a MISSING required secret must fail the pipeline loudly (never fake).
- Rotation: if any value is suspected exposed → revoke first (BotFather `/revoke`; Cloudflare token page), then set fresh value, re-run smoke test, record in audit log.
- Telegram user IDs are personal data: never commit them; the worker's owner ID stays a cloud-side var.
