"""Environment-backed configuration with safe trading defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigurationError(ValueError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    owner_ids: tuple[int, ...]
    admin_ids: tuple[int, ...] = ()
    trading_mode: str = "paper"
    live_trading_enabled: bool = False
    scan_interval_seconds: int = 300
    market_intervals: tuple[str, ...] = ("15m", "1h", "4h")
    minimum_signal_score: int = 70


def _parse_owner_ids(raw: str) -> tuple[int, ...]:
    if not raw.strip():
        return ()
    values: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item.isdigit():
            raise ConfigurationError("OWNER_IDS must be comma-separated numeric Telegram user IDs")
        values.append(int(item))
    if len(set(values)) != len(values):
        raise ConfigurationError("OWNER_IDS must not contain duplicates")
    return tuple(values)


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    """Load settings from the provided mapping or process environment.

    The token is required for runtime startup. Trading is forced to paper mode
    in this foundation stage; live execution is not configurable here.
    """
    env = os.environ if environ is None else environ
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ConfigurationError("TELEGRAM_BOT_TOKEN is required")
    if any(ch.isspace() for ch in token):
        raise ConfigurationError("TELEGRAM_BOT_TOKEN must not contain whitespace")

    owner_ids = _parse_owner_ids(env.get("OWNER_IDS", ""))
    admin_ids = _parse_owner_ids(env.get("ADMIN_IDS", ""))
    if set(owner_ids) & set(admin_ids):
        raise ConfigurationError("OWNER_IDS and ADMIN_IDS must not overlap")
    requested_mode = env.get("TRADING_MODE", "paper").strip().lower()
    if requested_mode != "paper":
        raise ConfigurationError("Only TRADING_MODE=paper is supported in this stage")

    return Settings(telegram_bot_token=token, owner_ids=owner_ids, admin_ids=admin_ids)
