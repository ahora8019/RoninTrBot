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
    commands = {
        command
        for handlers in application.handlers.values()
        for handler in handlers
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }
    assert {"start", "panel", "status", "help"}.issubset(commands)


import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.telegram.handlers import callback_handler


def test_member_cannot_open_admin_settings_even_with_forged_callback():
    query = SimpleNamespace(
        data="settings",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=999),
    )
    settings = Settings(
        telegram_bot_token="123456:ABCDEF1234567890abcdef",
        owner_ids=(123,),
        admin_ids=(456,),
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"settings": settings}))

    asyncio.run(callback_handler(update, context))

    query.answer.assert_awaited_once_with("Access denied.", show_alert=True)
    query.edit_message_text.assert_not_awaited()


def test_admin_can_open_settings_callback():
    query = SimpleNamespace(
        data="settings",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=456),
    )
    settings = Settings(
        telegram_bot_token="123456:ABCDEF1234567890abcdef",
        owner_ids=(123,),
        admin_ids=(456,),
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"settings": settings}))

    asyncio.run(callback_handler(update, context))

    query.answer.assert_awaited_once_with()
    query.edit_message_text.assert_awaited_once()
    assert "Live execution: DISABLED" in query.edit_message_text.await_args.args[0]
