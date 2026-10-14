import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.ext import CommandHandler

from app.core.config import Settings
from app.main import build_application
from app.market_data.lbank import Candle
from app.telegram.handlers import callback_handler


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
    assert {"start", "panel", "status", "market", "help"}.issubset(commands)


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


def test_member_can_open_market_callback_and_get_read_only_candles():
    query = SimpleNamespace(
        data="market",
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
    candles = [
        Candle(1_800_000_000, Decimal("100"), Decimal("110"), Decimal("90"), Decimal("105"), Decimal("5")),
        Candle(1_800_000_900, Decimal("105"), Decimal("112"), Decimal("101"), Decimal("108"), Decimal("6")),
    ]
    market_client = SimpleNamespace(fetch_ohlcv=lambda symbol, interval, limit: candles)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"settings": settings, "market_data_client": market_client}
        )
    )

    asyncio.run(callback_handler(update, context))

    query.answer.assert_awaited_once_with()
    query.edit_message_text.assert_awaited_once()
    report = query.edit_message_text.await_args.args[0]
    assert "LBank Spot — BTC_USDT (15m)" in report
    assert "Close: 108" in report
    assert "order execution is disabled" in report


def test_invalid_market_data_request_does_not_call_exchange():
    query = SimpleNamespace(
        data="market",
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
    market_client = SimpleNamespace(fetch_ohlcv=AsyncMock())
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"settings": settings, "market_data_client": market_client}
        )
    )

    # The callback uses a fixed, valid default pair; the fake adapter returns no data
    # by raising a validation-shaped error, which must be handled without crashing.
    from app.market_data.lbank import MarketDataValidationError
    market_client.fetch_ohlcv = lambda *args: (_ for _ in ()).throw(MarketDataValidationError("bad payload"))

    asyncio.run(callback_handler(update, context))

    assert "temporarily unavailable" in query.edit_message_text.await_args.args[0]
    assert "No trading action was taken" in query.edit_message_text.await_args.args[0]


def test_market_command_rejects_more_than_two_args():
    reply = []
    update = SimpleNamespace(
        effective_message=SimpleNamespace(reply_text=AsyncMock(side_effect=lambda text: reply.append(text))),
        effective_user=SimpleNamespace(id=999),
    )
    from app.telegram.handlers import market_command
    context = SimpleNamespace(args=["btc_usdt", "15m", "extra"])

    asyncio.run(market_command(update, context))

    assert reply and reply[0].startswith("Usage: /market")


def test_market_command_reports_invalid_symbol_without_exchange_call():
    reply = []
    update = SimpleNamespace(
        effective_message=SimpleNamespace(reply_text=AsyncMock(side_effect=lambda text: reply.append(text))),
        effective_user=SimpleNamespace(id=999),
    )
    calls = []

    def fake_fetch(symbol, interval, limit):
        calls.append(symbol)
        from app.market_data.lbank import _SYMBOL_RE
        if not _SYMBOL_RE.fullmatch(symbol.strip().lower()):
            raise ValueError("symbol must use LBank pair format")

    from app.telegram.handlers import market_command
    context = SimpleNamespace(
        args=["bad-symbol-$$$"],
        application=SimpleNamespace(bot_data={"market_data_client": SimpleNamespace(fetch_ohlcv=fake_fetch)}),
    )

    asyncio.run(market_command(update, context))

    assert len(calls) == 1  # validated inside the adapter boundary
    assert reply and reply[0].startswith("Invalid market request:")


def test_member_keyboard_hides_admin_actions_while_admin_keyboard_shows_them():
    from app.telegram.handlers import _keyboard
    member_buttons = {b.callback_data for row in _keyboard("MEMBER").inline_keyboard for b in row}
    admin_buttons = {b.callback_data for row in _keyboard("ADMIN").inline_keyboard for b in row}

    assert "settings" not in member_buttons and "management" not in member_buttons
    assert "settings" in admin_buttons and "management" in admin_buttons
