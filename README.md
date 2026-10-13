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
