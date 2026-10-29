# RoninTrBot Telegram Panel Runtime Audit

Audit date: 2026-10-29
Scope: Telegram control-panel startup and safe validation only. No deployment, Cloudflare setting change, secret read/write, Telegram API call using a credential, or financial action was performed.

## Executive status

- **Panel runtime code review:** PASS (static inspection of repository source; not an end-to-end runtime test).
- **Webhook compatibility:** PARTIAL / NOT VERIFIED at runtime. The Worker implements an authenticated webhook receiver; actual Telegram webhook registration and delivery have not been verified.
- **Telegram token validity:** NOT VERIFIED. The token value was not read, echoed, or used by this audit.
- **Owner configuration:** BLOCKED pending a controlled Production plan and explicit approval. The code relies on a Cloudflare Worker variable and has no first-run owner-claim flow.
- **Production deployment:** BLOCKED pending explicit user approval.
- **Live trading:** NOT enabled by this audit; no exchange connection or order action was attempted. Source health response advertises engine offline and exchange disconnected, but live configuration state has not been independently revalidated at runtime.

## Phase 1 — Runtime code path

Reviewed source: `workers/ronin-panel/worker.js` on branch `feat/cloudflare-deploy-sha-verification`.
Source URL: https://github.com/ahora8019/RoninTrBot/blob/feat/cloudflare-deploy-sha-verification/workers/ronin-panel/worker.js
Reviewed configuration: `workers/ronin-panel/wrangler.jsonc`.
Source URL: https://github.com/ahora8019/RoninTrBot/blob/feat/cloudflare-deploy-sha-verification/workers/ronin-panel/wrangler.jsonc

### Commands

- `/start`: handled in the `update.message` branch; calls `getRole(user, env)`, then `bot.sendMessage(chatId, dashboard(role), mainKeyboard(role))`.
- `/panel`: same dashboard path as `/start`; it does not require a persisted owner record.
- `/status`: calls `systemStatus(role)` and `statusKeyboard(role)`.
- Admin/management actions are callback-query routes in the same Worker. `getRole()` returns OWNER only when the incoming Telegram user ID matches `env.OWNER_TELEGRAM_ID`; ADMIN is based on comma-separated `env.ADMIN_TELEGRAM_IDS`; all other users are MEMBER. `isAllowed()` gates the admin callbacks.
- Static source locations: command handlers are near the top of `workers/ronin-panel/worker.js`; Telegram client class around lines 633–745; role and authorization logic around lines 749–834 in the reviewed file version. Line numbers may move on later commits.

### Transport and Telegram token

- **Mechanism:** webhook push delivery, not long polling. The Worker accepts Telegram updates via HTTP POST. It checks `WEBHOOK_SECRET` against the `X-Telegram-Bot-Api-Secret-Token` header before parsing the update. Missing webhook secret returns 503; mismatch returns 403.
- **Required secret:** `TELEGRAM_BOT_TOKEN`, read from the Worker environment as `env.TELEGRAM_BOT_TOKEN`. The `Telegram` client constructs Telegram Bot API requests from this value; for example, `sendMessage` calls Telegram's API.
- **Required webhook secret:** `WEBHOOK_SECRET`, also a Worker secret. Telegram's webhook must be registered externally with the matching `secret_token` and the correct Worker URL. The repository Worker does not itself call `setWebhook` or implement polling.
- **KV:** `USERS_KV` is used to record private-chat audience metadata and broadcast state. It is not the owner registry.
- `OWNER_TELEGRAM_ID` and `ADMIN_TELEGRAM_IDS` are environment variables, not KV/D1/R2 records. No owner-claim or first-user bootstrap path was found in the reviewed command/role flow.
- The code's `registerUser()` stores a record under `user:<chatId>` containing the chat/Telegram ID, username, first name, active flag, and update timestamp. This is personal data; KV access and retention should be limited. It does not promote a user to owner.
- If `OWNER_TELEGRAM_ID` is missing/empty, the role comparison cannot grant OWNER to a normal Telegram user. `/start` may still enter its handler, but the sender is treated as MEMBER and owner-only functions remain unavailable. Therefore, do not consider first-run setup complete without configuring the owner variable out of band.
- The Worker currently returns a generic success JSON after processing a message without checking the Telegram `sendMessage` result's `ok` field. A webhook HTTP 200 alone therefore does not prove the user received the reply. This should be considered in future runtime/error-handling improvements.

### Phase 1 status table

| Item | Status | Evidence | Blocker / risk | Next action |
|---|---|---|---|---|
| `/start` route | PASS (static code) | Worker handler calls dashboard + main keyboard | No live Telegram interaction | Run only after approved deployment and webhook validation |
| `/panel` route | PASS (static code) | Same dashboard handler | No live Telegram interaction | Same |
| `/status` route | PASS (static code) | Worker handler calls systemStatus + status keyboard | No live Telegram interaction | Same |
| Admin callbacks | PASS (static code) | `getRole` + `isAllowed` checks | Actual owner/admin vars unverified | Verify configured roles without exposing values |
| Webhook vs polling | PASS (static code) | POST handler and secret-header check; no polling loop found | Telegram's registered webhook not checked | Check webhook info safely once a supported credential-backed execution path is approved |
| Token binding/read path | PASS (static code) | `env.TELEGRAM_BOT_TOKEN` used by Telegram client | Token validity NOT VERIFIED | Use a controlled, redacted `getMe` probe |
| Owner setup model | PASS (static code) | `env.OWNER_TELEGRAM_ID`; no bootstrap route found | Runtime value and scope NOT VERIFIED | Set via approved Cloudflare configuration plan |
| User/audience storage | PASS (static code) | `USERS_KV`, `registerUser()` | KV privacy/retention considerations | Keep values out of reports and logs |

## Phase 2 — Safe token validation

### Proposed safe test

Use a controlled execution path that reads `TELEGRAM_BOT_TOKEN` only inside the Worker/runtime secret store and calls Telegram's `getMe`. The result processor must return only:
- boolean API success,
- public bot numeric ID,
- public bot username,
- `can_join_groups` (if present),
- a coarse error category on failure.

It must never return the token, request URL, request headers, chat IDs, user messages, private-chat data, or the raw Telegram response. Avoid logging the URL because Telegram bot API URLs contain the token. Do not add a public unauthenticated diagnostics endpoint; if a diagnostic route is needed, require a separate operator-only authorization gate, rate-limit it, and remove/disable it after use.

### Execution result

**BLOCKED / NOT VERIFIED.** This audit did not read or change the Worker secret and did not make a Telegram Bot API request. The deployed Worker is known from the previous verified account inspection to be a test harness, not this repository implementation; therefore its health endpoint cannot validate the repository bot token. A production `getMe` probe cannot safely be performed against the current harness without first changing runtime code/deployment, which is prohibited until the user approves the deployment plan.

Required path: after the plan below is approved, use a reviewed, short-lived, operator-authorized diagnostic that invokes `getMe` server-side and returns only the allowlisted fields above, or run an equivalent secret-backed check in a controlled environment that is already authorized to access the secret. No token needs to be pasted into chat or printed to a terminal. If neither execution path is available, keep this item NOT VERIFIED.

## Phase 3 — PR #28 release workflow review

PR: https://github.com/ahora8019/RoninTrBot/pull/28
Branch: `feat/cloudflare-deploy-sha-verification`
Head commit: `09da792afa33c75c6e93c0e56d79e2441604c5c9`
Workflow file at that commit: `.github/workflows/deploy-worker.yml`
Workflow source: https://github.com/ahora8019/RoninTrBot/blob/feat/cloudflare-deploy-sha-verification/.github/workflows/deploy-worker.yml

Static review findings:
- Deploy is triggered only by `workflow_dispatch`; the deploy and secrets-preflight jobs also require `github.ref == refs/heads/main`. Push/merge no longer deploys automatically.
- Wrangler deploy runs from the workflow's checked-out `$GITHUB_SHA`, injects that SHA as `GIT_SHA`, then calls `scripts/smoke_test.py --expected-sha "$GITHUB_SHA"`.
- The smoke test requires HTTP 200, an exact 40-character SHA match, required health fields, and HTTP 403 for an unauthenticated synthetic POST.
- Required GitHub Actions secrets are named `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`; the preflight fails if absent. The workflow also uses the `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`, and owner/admin configuration already managed in the Cloudflare Worker, but does not print their values.
- The workflow prints the deployed Git SHA and smoke-test status. Wrangler output is expected to identify the script/version, but the workflow does not explicitly emit a single structured deployment summary with environment + Worker name + deployment/version ID + result. Recommendation only: add an explicit redacted summary step that prints these non-secret fields.
- Rollback instructions point to Wrangler deployment history and `docs/deployment/rollback.md`. A failed post-deploy smoke test does not itself automatically restore the prior version; rollback remains an operator action and must be handled as a separate, approved change.
- The PR body reports 8/8 visible CI checks as passing for the head SHA, but the current combined-status API query returned an empty statuses list. Therefore current CI status is **NOT VERIFIED by this audit run**; the PR's historical statement is not treated as fresh proof.
- No workflow or production setting was changed.

## Phase 4 — Proposed deployment and owner setup plan (NOT EXECUTED)

**Target:** Cloudflare Worker `ronintrbot` in the project account, environment `main/default production Worker`, URL currently documented as `https://ronintrbot.ahora-project.workers.dev`. Verify the exact live target immediately before execution; the legacy `ronin-panel.ronin...workers.dev` endpoint is not the target.

1. **Review target:** PR #28 remains open Draft at head `09da792afa33c75c6e93c0e56d79e2441604c5c9`. Do not deploy that SHA unless it is the reviewed, approved release candidate and includes the intended current panel source. Confirm the chosen branch/PR and exact SHA immediately before deployment.
2. **Preflight:** inspect current PR review state and live deployment inventory; confirm current Worker name, domain, binding names, secret presence (existence only), webhook target, and trading disabled state. Do not reveal secret values.
3. **CI:** require all applicable checks to be green for the exact approved SHA. The currently queried combined-status response was empty, so this gate is not yet met on current evidence.
4. **Credentials/bindings (names only):** GitHub Actions `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`; Worker secrets `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`; Worker variables `OWNER_TELEGRAM_ID`, optional `ADMIN_TELEGRAM_IDS`, and release metadata `GIT_SHA`; KV binding `USERS_KV`. Never put the owner ID or secret values in source control or logs.
5. **Owner setup:** set `OWNER_TELEGRAM_ID` as a Cloudflare Worker variable using the owner ID already provided by the operator, only after explicit approval. Do not implement an automatic first-user claim. Verify the owner role using a controlled check that emits only role success/failure, not the ID.
6. **Webhook:** register/update Telegram webhook to the exact approved Worker URL with the matching `WEBHOOK_SECRET` as Telegram's webhook `secret_token`. Do not start polling. Before changing webhook state, inspect current webhook metadata without exposing URL/token fields; coordinate the migration to avoid losing updates.
7. **Deploy:** invoke the manual Deploy Worker workflow on `main` at the exact approved SHA. No direct API upload and no push-triggered deployment.
8. **Post-deploy checks:** verify health reports the exact SHA; run the synthetic unauthenticated POST and require 403; then test `getMe` via the controlled redacted method. Do not run exchange calls or trading tests.
9. **Rollback:** if health/SHA/auth checks fail, stop the Telegram command tests. Record the failing SHA and deployment/version ID; with explicit operator approval, use the previous known-good Wrangler version rollback and rerun read-only smoke checks. Do not alter KV data as part of code rollback.
10. **Success criteria:** exact approved SHA is deployed; health check is correct; unauthenticated POST gets 403; redacted `getMe` succeeds; webhook metadata points to the intended Worker without leaking its URL/token; `/start`, `/panel`, and `/status` each receive expected replies; owner role is confirmed; trading engine remains offline, exchange disconnected, and live trading disabled.

### Approval gate

No deployment, webhook migration, secret/binding mutation, owner configuration, or rollback was performed. Before any such action, the user must explicitly approve the exact target SHA, production Worker/environment, owner-variable update, webhook migration, and rollback authority. This document does not itself constitute deployment approval.

## Final status matrix

| Item | Status | Evidence | Blocker or risk | Next action | User approval required |
|---|---|---|---|---|---|
| Repository command routes | PASS (static review) | `workers/ronin-panel/worker.js` source | No end-to-end test | Test after approved release | Yes, for release |
| Webhook receiver implementation | PASS (static review) | POST + secret-header check | Current live registration not inspected | Controlled webhook metadata check | Yes, before webhook changes |
| Telegram token validity | NOT VERIFIED | No `getMe` call performed | Live code is not deployed; secret must not be exposed | Controlled redacted `getMe` | Yes, if it requires runtime change |
| Owner configuration | BLOCKED | Code reads `OWNER_TELEGRAM_ID`; no bootstrap found | Current live variable value not verified | Configure via approved Cloudflare variable change | Yes |
| PR #28 manual-only behavior | PASS (static review) | Workflow triggers/conditions reviewed at head SHA | PR remains Draft/open | Review/approve PR; refresh CI | Yes, to merge/release |
| Exact tested SHA deployment gate | PASS (static review) | `GIT_SHA` + `--expected-sha` smoke check | No actual deployment tested | Run only through manual workflow | Yes |
| Deployment summary fields | PARTIAL | SHA printed; Wrangler emits deployment output | Environment/name/version/result not guaranteed as one structured summary | Recommend workflow summary enhancement | Yes, before workflow edit |
| Current CI status | NOT VERIFIED | Combined-status query returned no statuses | PR description has historical 8/8 claim only | Refresh check-runs/status on exact SHA | No, read-only |
| Live `/start`, `/panel`, `/status` | BLOCKED / NOT RUN | No real Telegram interaction | Production release and webhook not approved/verified | Run the minimal test matrix after approval | Yes |
| Live trading disabled | NOT VERIFIED at live runtime; source defaults indicate offline | Repository health source says engine offline/exchange disconnected; prior test harness health said `trading_enabled: false` | Current deployed harness is not the repository Worker | Recheck health/config after release without exchange access | Yes, before deployment |
| Production deployment | BLOCKED | No deployment action performed | Explicit approval required | Await approval of exact plan/SHA | Yes |
