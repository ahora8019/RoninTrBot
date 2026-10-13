"""Telegram commands and callback handlers with read-only market-data access."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from app.core.config import Settings
from app.market_data.lbank import LBankMarketDataClient, MarketDataError
from app.telegram.auth import ADMIN, OWNER, action_allowed, resolve_role

logger = logging.getLogger(__name__)

PAGE_TEXT = {
    "home": "RoninTrBot v1.0\nControl panel is online. Market data is read-only. Trading execution is disabled.",
    "help": "Commands: /start, /panel, /status, /market [symbol] [15m|1h|4h], /help\nExample: /market btc_usdt 15m",
    "status": "Bot interface: ONLINE\nMarket data: PUBLIC READ-ONLY (on demand)\nStrategy: NOT CONNECTED\nRisk engine: NOT CONNECTED\nPaper trading: NOT CONNECTED\nExecution: DISABLED",
    "paper": "Paper Trading is not connected yet. No trades are simulated.",
    "trades": "Trade history is not connected yet.",
    "strategy": "Strategy engine is not connected yet.",
    "settings": "Safe configuration view. Trading mode: PAPER. Live execution: DISABLED.",
    "management": "Management controls are not connected. No engine actions were performed.",
}

DEFAULT_MARKET_SYMBOL = "btc_usdt"
DEFAULT_MARKET_INTERVAL = "15m"


def _role(update: Update, settings: Settings) -> str:
    user_id = update.effective_user.id if update.effective_user else None
    return resolve_role(user_id, settings.owner_ids, settings.admin_ids)


def _keyboard(role: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("⚡ Status", callback_data="status"), InlineKeyboardButton("📊 Market Data", callback_data="market")],
        [InlineKeyboardButton("🧪 Paper", callback_data="paper"), InlineKeyboardButton("📈 Trades", callback_data="trades")],
        [InlineKeyboardButton("🧠 Strategy", callback_data="strategy")],
        [InlineKeyboardButton("❔ Help", callback_data="help")],
    ]
    if role in {OWNER, ADMIN}:
        rows.insert(2, [InlineKeyboardButton("⚙️ Settings", callback_data="settings"), InlineKeyboardButton("🛡 Management", callback_data="management")])
    return InlineKeyboardMarkup(rows)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    role = _role(update, settings)
    if update.effective_message:
        await update.effective_message.reply_text(
            f"🥷 RoninTrBot v1.0\nRole: {role}\nMarket data: public read-only\nLive trading: DISABLED",
            reply_markup=_keyboard(role),
        )


async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_command(update, context)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(PAGE_TEXT["status"])


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(PAGE_TEXT["help"])


async def _market_report(context: ContextTypes.DEFAULT_TYPE, symbol: str, interval: str) -> str:
    client: LBankMarketDataClient = context.application.bot_data["market_data_client"]
    try:
        candles = await asyncio.to_thread(client.fetch_ohlcv, symbol, interval, 2)
    except ValueError as exc:
        return f"Invalid market request: {exc}\nUse /market btc_usdt 15m (or 1h / 4h)."
    except MarketDataError as exc:
        logger.warning("Market-data request failed: %s", type(exc).__name__)
        return "Market data is temporarily unavailable or failed validation. No trading action was taken."

    latest = candles[-1]
    timestamp = datetime.fromtimestamp(latest.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"📊 LBank Spot — {symbol.upper()} ({interval})\n"
        f"Latest candle: {timestamp}\n"
        f"Open: {latest.open}\nHigh: {latest.high}\nLow: {latest.low}\n"
        f"Close: {latest.close}\nVolume: {latest.volume}\n\n"
        "Read-only market data. This is not a trading signal; order execution is disabled."
    )


async def market_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    args = context.args
    if len(args) > 2:
        await update.effective_message.reply_text(
            "Usage: /market [symbol] [15m|1h|4h]\nExample: /market btc_usdt 15m"
        )
        return
    symbol = args[0] if args else DEFAULT_MARKET_SYMBOL
    interval = args[1] if len(args) > 1 else DEFAULT_MARKET_INTERVAL
    report = await _market_report(context, symbol, interval)
    await update.effective_message.reply_text(report)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    settings: Settings = context.application.bot_data["settings"]
    role = _role(update, settings)
    action = query.data or ""
    if not action_allowed(action, role):
        await query.answer("Access denied.", show_alert=True)
        return
    await query.answer()
    if action == "market":
        text = await _market_report(context, DEFAULT_MARKET_SYMBOL, DEFAULT_MARKET_INTERVAL)
    else:
        text = PAGE_TEXT.get(action)
    if text is None:
        await query.edit_message_text("Unknown action. Nothing was executed.")
        return
    await query.edit_message_text(text, reply_markup=_keyboard(role))


def register_handlers(application: Application, settings: Settings) -> None:
    """Attach settings, configure the public market-data adapter, and register commands."""
    application.bot_data["settings"] = settings
    application.bot_data.setdefault("market_data_client", LBankMarketDataClient())
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("panel", panel_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("market", market_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
