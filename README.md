# RoninTrBot v1.0

Python-first Telegram trading assistant for LBank, developed in controlled stages.

## Fixed scope

1. **Foundation** — repository structure, configuration contract, logging, error handling, and tests.
2. **Telegram interface** — commands, owner/admin/member authorization, and safe settings.
3. **Market data** — market data adapter and normalized OHLCV data; read-only at this stage.
4. **Strategy engine** — 15m/1h/4h analysis, indicators, regime checks, and score threshold (70).
5. **Risk engine** — position sizing, dynamic stop-loss/take-profit calculations, and hard safety limits.
6. **Validation** — unit tests, backtest, out-of-sample (OOS), walk-forward (WF), and paper trading.
7. **Release readiness** — deployment setup, secrets review, operational checks, and documentation.

## Scope rules

- Python is the primary language.
- No features outside this roadmap are added during implementation without explicit approval.
- No live orders during development or validation.
- API credentials and tokens must never be committed to the repository.
- Exchange connectivity starts read-only; live trading remains disabled until validation is complete and explicitly approved.
- Each stage must meet its acceptance criteria before the next stage begins.

## Initial operating assumptions

- Telegram bot token is provided through `TELEGRAM_BOT_TOKEN`.
- Exchange credentials, when needed, are supplied through environment variables and are not stored in source control.
- The target exchange is LBank; exact supported endpoints must be verified before implementation.
- The initial market-data intervals are 15m, 1h, and 4h, with a five-minute scan cadence where operationally appropriate.

## Stage 1 acceptance criteria

- Clear, minimal Python package structure.
- Configuration validation fails safely when required settings are absent or invalid.
- Logging does not expose secrets.
- Automated tests cover configuration and core safety defaults.
- No exchange order-placement code is present.

## Out of scope for v1.0 unless explicitly approved

- Extra dashboards or web panels.
- AI/LLM strategy generation.
- Automatic strategy changes or self-modifying behavior.
- Multi-exchange support beyond the agreed LBank target.
- Live trading before validation and explicit approval.

## Stage 2 acceptance criteria

- Telegram commands `/start`, `/panel`, `/status`, and `/help` are registered.
- Role resolution is based on numeric Telegram user IDs configured through `OWNER_IDS` and `ADMIN_IDS`; owners take precedence and overlapping lists are rejected.
- Callback actions are authorized server-side; unknown actions fail closed.
- The interface clearly marks market data, strategy, risk, and execution as disconnected until their later stages.
- Live trading remains disabled; no exchange order placement or engine control is implemented in this stage.
- Automated tests cover role resolution and authorization policy.
- Do not start polling with the existing bot token until the current Cloudflare webhook/polling setup is deliberately migrated; Telegram does not support simultaneous webhook and long-polling updates for the same bot.

## Stage 3 acceptance criteria

- Fetch public LBank V2 spot K-line data for `15m`, `1h`, and `4h` using the documented `GET /v2/kline.do` endpoint.
- Normalize OHLCV values to immutable candle records with decimal prices and Unix-second timestamps.
- Reject malformed payloads, invalid symbols/intervals/limits, duplicate or future timestamps, stale latest candles, non-finite values, and inconsistent OHLC values.
- Use bounded retries for transient HTTP/network failures and a configurable per-client minimum request interval.
- Keep this adapter public and read-only: no API keys, account endpoints, or order placement.
- Automated tests use injected fake transports; tests do not require exchange credentials or live network access.
