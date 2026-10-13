"""Runtime entry point for the RoninTrBot Telegram interface."""

from __future__ import annotations

import asyncio
import logging

from telegram import Bot
from telegram.ext import Application

from app.core.config import ConfigurationError, Settings, load_settings
from app.core.logging_setup import configure_logging
from app.telegram.handlers import register_handlers

logger = logging.getLogger(__name__)


async def _webhook_is_configured(token: str) -> bool:
    """Check current Telegram transport without changing webhook configuration."""
    async with Bot(token=token) as bot:
        info = await bot.get_webhook_info()
        return bool(info.url)


def build_application(settings: Settings | None = None) -> Application:
    settings = settings or load_settings()
    application = Application.builder().token(settings.telegram_bot_token).build()
    register_handlers(application, settings)
    return application


def main() -> None:
    configure_logging()
    try:
        settings = load_settings()
        try:
            webhook_active = asyncio.run(_webhook_is_configured(settings.telegram_bot_token))
        except Exception as exc:  # pre-flight network/transport failure: fail closed
            logger.error(
                "Startup refused: webhook pre-flight check failed (%s). Fix connectivity, then retry.",
                type(exc).__name__,
            )
            raise SystemExit(3) from None
        if webhook_active:
            logger.error(
                "Startup refused: this bot already has a Telegram webhook. "
                "Migrate the existing Cloudflare webhook deliberately before enabling polling."
            )
            raise SystemExit(3)
        application = build_application(settings)
    except ConfigurationError as exc:
        logger.error("Invalid runtime configuration: %s", exc)
        raise SystemExit(2) from None
    logger.info("Starting RoninTrBot Telegram interface; live trading remains disabled.")
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
