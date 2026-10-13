"""Telegram commands and callback handlers; trading engines are not connected here."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from app.core.config import Settings
from app.telegram.auth import ADMIN, OWNER, action_allowed, resolve_role

logger = logging.getLogger(__name__)

PAGE_TEXT = {
    "home": "RoninTrBot v1.0\nControl panel is online. Trading is not connected.",
    "help": "Commands: /start, /panel, /status, /help",
    "status": "Bot interface: ONLINE\nMarket data: NOT CONNECTED\nStrategy: NOT CONNECTED\nRisk engine: NOT CONNECTED\nExecution: DISABLED",
    "paper": "Paper Trading is not connected yet. No trades are simulated.",
    "trades": "Trade data is not connected yet.",
    "strategy": "Strategy engine is not connected yet.",
    "settings": "Safe configuration view. Trading mode: PAPER. Live execution: DISABLED.",
    "management": "Management controls are not connected. No engine actions were performed.",
}


def _role(update: Update, settings: Settings) -> str:
    user_id = update.effective_user.id if update.effective_user else None
    return resolve_role(user_id, settings.owner_ids, settings.admin_ids)


def _keyboard(role: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("⚡ Status", callback_data="status"), InlineKeyboardButton("🧪 Paper", callback_data="paper")],
        [InlineKeyboardButton("📈 Trades", callback_data="trades"), InlineKeyboardButton("🧠 Strategy", callback_data="strategy")],
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
            f"🥷 RoninTrBot v1.0\nRole: {role}\nLive trading: DISABLED",
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
    text = PAGE_TEXT.get(action)
    if text is None:
        await query.edit_message_text("Unknown action. Nothing was executed.")
        return
    await query.edit_message_text(text, reply_markup=_keyboard(role))


def register_handlers(application: Application, settings: Settings) -> None:
    """Attach settings and register the minimal stage-2 command surface."""
    application.bot_data["settings"] = settings
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("panel", panel_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
