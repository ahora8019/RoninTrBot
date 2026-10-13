"""Runtime entry point for the RoninTrBot Telegram interface."""

from __future__ import annotations

import logging

from telegram.ext import Application

from app.core.config import ConfigurationError, load_settings
from app.core.logging_setup import configure_logging
from app.telegram.handlers import register_handlers

logger = logging.getLogger(__name__)


def build_application() -> Application:
    settings = load_settings()
    application = Application.builder().token(settings.telegram_bot_token).build()
    register_handlers(application, settings)
    return application


def main() -> None:
    configure_logging()
    try:
        application = build_application()
    except ConfigurationError as exc:
        logger.error("Invalid runtime configuration: %s", exc)
        raise SystemExit(2) from None
    logger.info("Starting RoninTrBot Telegram interface; live trading remains disabled.")
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
