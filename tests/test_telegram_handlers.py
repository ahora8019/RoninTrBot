from telegram.ext import CommandHandler

from app.core.config import Settings
from app.main import build_application


def test_expected_telegram_commands_are_registered():
    settings = Settings(
        telegram_bot_token="123456:ABCDEF1234567890abcdef",
        owner_ids=(123,),
        admin_ids=(456,),
    )
    application = build_application(settings)
    try:
        commands = {
            command
            for handlers in application.handlers.values()
            for handler in handlers
            if isinstance(handler, CommandHandler)
            for command in handler.commands
        }
        assert {"start", "panel", "status", "help"}.issubset(commands)
    finally:
        # Application was constructed but never initialized or connected to Telegram.
        pass
