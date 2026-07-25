# -*- coding: utf-8 -*-

import os
import re
import logging
from datetime import datetime
from typing import Dict, Any

import aiohttp
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Index, func
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)

# SECURITY: put your real token in an environment variable instead of hardcoding it.
# The token below was exposed in a previous version of this file -- regenerate it
# via @BotFather (/revoke) and export the new one as BOT_TOKEN before relying on this.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8232467294:AAE5ijjTdqoJvxYfoTsmCzM60DF7RPZ2xG4")

HOLWIN_INVITE_CODE = "WLRPSY"
REX_INVITE_CODE = "O6NVYX"

HOLWIN_BASE = "https://www.holwin123.top"
HOLWIN_DI = "88dd52c70e7b377527be01c39f5a0a4f"
HOLWIN_VTOKEN = "18667bd921478af5fe5f6506865e4f8a"

REX_BASE = "https://rcapi.rexproearn.com"
REX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://rch5.rexproearn.com",
    "Referer": "https://rch5.rexproearn.com/",
}

DATABASE_URL = "sqlite:///registrations.db"

logging.basicConfig(
    format="[%(asctime)s] %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Registration(Base):
    __tablename__ = "registrations"
    id = Column(Integer, primary_key=True)
    mobile = Column(String(20), nullable=False)
    platform = Column(String(20), nullable=False)
    invite_used = Column(String(20), nullable=False)
    telegram_id = Column(Integer, nullable=False)
    registered_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_platform", "platform"),
        Index("idx_telegram_id", "telegram_id"),
        Index("idx_registered_at", "registered_at"),
    )


Base.metadata.create_all(engine)

MOBILE, OTP, PASSWORD, CONFIRM = range(4)

# BUG FIX: the old regex was r"([_*[]()~`>#+-=|{}.!\\])" which is invalid
# (unescaped '[' inside a character class, and the replacement used \u0001
# instead of a backreference to the captured character). This version
# correctly escapes every MarkdownV2 special character.
_MDV2_SPECIAL = re.compile(r'([_*\[\]()~`>#+\-=|{}.!\\])')


def esc(text: str) -> str:
    return _MDV2_SPECIAL.sub(r'\\\1', str(text))


def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏠 Holwin", callback_data="platform_holwin"),
            InlineKeyboardButton("📈 Rexproearn", callback_data="platform_rex"),
        ],
        [
            InlineKeyboardButton("📊 Stats", callback_data="stats_btn"),
            InlineKeyboardButton("📋 My Registrations", callback_data="my_btn"),
            InlineKeyboardButton("❓ Help", callback_data="help_btn"),
        ],
    ])


def back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]
    ])


def otp_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Resend OTP", callback_data="resend_otp")],
        [InlineKeyboardButton("✏️ Change Mobile", callback_data="change_mobile")],
        [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")],
    ])


def confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="confirm_reg")],
        [InlineKeyboardButton("✏️ Change Mobile", callback_data="change_mobile")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_reg")],
    ])


def db_session():
    return SessionLocal()


def save_registration(mobile: str, platform: str, invite: str, telegram_id: int):
    db: Session = db_session()
    try:
        db.add(Registration(
            mobile=mobile,
            platform=platform,
            invite_used=invite,
            telegram_id=telegram_id
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"DB save error: {e}")
        raise
    finally:
        db.close()


def get_stats():
    db = db_session()
    try:
        total = db.query(func.count(Registration.id)).scalar() or 0
        holwin = db.query(func.count(Registration.id)).filter(Registration.platform == "holwin").scalar() or 0
        rex = db.query(func.count(Registration.id)).filter(Registration.platform == "rex").scalar() or 0
        recent = db.query(Registration).order_by(Registration.registered_at.desc()).limit(10).all()
        return total, holwin, rex, recent
    finally:
        db.close()


def get_user_stats(user_id: int):
    db = db_session()
    try:
        total = db.query(func.count(Registration.id)).filter(Registration.telegram_id == user_id).scalar() or 0
        holwin = db.query(func.count(Registration.id)).filter(
            Registration.telegram_id == user_id,
            Registration.platform == "holwin"
        ).scalar() or 0
        rex = db.query(func.count(Registration.id)).filter(
            Registration.telegram_id == user_id,
            Registration.platform == "rex"
        ).scalar() or 0
        return total, holwin, rex
    finally:
        db.close()


class HolwinClient:
    def __init__(self):
        self.session = None

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=20)
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://www.holwin123.top",
            "Referer": "https://www.holwin123.top/userRegister",
            "di": HOLWIN_DI,
            "vtoken": HOLWIN_VTOKEN,
        }
        self.session = aiohttp.ClientSession(headers=headers, timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session:
            await self.session.close()

    async def post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        async with self.session.post(f"{HOLWIN_BASE}{path}", json=payload) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception as e:
                logger.error(f"Holwin non-JSON response ({resp.status}): {e}")
                return {"code": -1, "msg": f"Invalid response from server (HTTP {resp.status})"}
            if not isinstance(data, dict):
                return {"code": -1, "msg": "Unexpected response format"}
            return data


class RexClient:
    def __init__(self):
        self.session = None

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=20)
        self.session = aiohttp.ClientSession(headers=REX_HEADERS, timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session:
            await self.session.close()

    async def post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        async with self.session.post(f"{REX_BASE}{path}", json=payload) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception as e:
                logger.error(f"Rex non-JSON response ({resp.status}): {e}")
                return {"code": -1, "msg": f"Invalid response from server (HTTP {resp.status})"}
            if not isinstance(data, dict):
                return {"code": -1, "msg": "Unexpected response format"}
            return data


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    msg = (
        "╔═══════════════════════════════════════╗\n"
        "║     💎  R E F E R R A L  B O T       ║\n"
        "╚═══════════════════════════════════════╝\n\n"
        "🚀 *Select your battle\\-ground:*\n\n"
        "┌─────────────────────────────┐\n"
        f"│  🏠 *Holwin*                │\n"
        f"│  Invite: `{esc(HOLWIN_INVITE_CODE)}`   │\n"
        "├─────────────────────────────┤\n"
        f"│  📈 *Rexproearn*            │\n"
        f"│  Invite: `{esc(REX_INVITE_CODE)}`      │\n"
        "└─────────────────────────────┘\n\n"
        "🛡️ *Features:*\n"
        "   🔐 OTP resend\n"
        "   ✏️ Change mobile\n"
        "   📊 Global stats\n"
        "   📋 Personal stats\n"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            msg,
            parse_mode="MarkdownV2",
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )
    else:
        await update.message.reply_text(
            msg,
            parse_mode="MarkdownV2",
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓ Help Center\n\n"
        "1\\. Choose a platform from the main menu\\.\n"
        "2\\. Enter your mobile number \\(10\\-15 digits\\)\\.\n"
        "3\\. Enter the OTP you receive\\.\n"
        "4\\. Set a password or type 'skip' for default\\.\n"
        "5\\. Confirm and register\\.\n\n"
        "📊 Use /stats to see all registrations\\.\n"
        "📋 Use /my to see your own registrations\\.\n"
        "🔄 Use /start to return to the main menu\\.\n"
        "❌ Use /cancel to abort any ongoing process\\.\n"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text,
            parse_mode="MarkdownV2",
            reply_markup=back_keyboard(),
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="MarkdownV2",
            reply_markup=back_keyboard(),
        )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total, holwin, rex, recent = get_stats()
    msg = (
        "📊 *Global Stats*\n\n"
        f"👥 Total: `{total}`\n"
        f"🏠 Holwin: `{holwin}`\n"
        f"📈 Rexproearn: `{rex}`\n\n"
        "🕒 *Last 10 Registrations:*\n"
    )
    if recent:
        for r in recent:
            msg += f"• `{esc(r.mobile)}` \\- {esc(r.platform.upper())} \\- {esc(r.registered_at.strftime('%Y-%m-%d %H:%M'))}\n"
    else:
        msg += "No registrations yet\\."

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="stats_btn")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")],
    ])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, parse_mode="MarkdownV2", reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode="MarkdownV2", reply_markup=kb)


async def my_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total, holwin, rex = get_user_stats(update.effective_user.id)
    msg = (
        "📋 *Your Registrations*\n\n"
        f"👤 Total: `{total}`\n"
        f"🏠 Holwin: `{holwin}`\n"
        f"📈 Rexproearn: `{rex}`"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, parse_mode="MarkdownV2", reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode="MarkdownV2", reply_markup=kb)


async def platform_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    platform = q.data.split("_")[1]
    context.user_data["platform"] = platform
    context.user_data["invite"] = HOLWIN_INVITE_CODE if platform == "holwin" else REX_INVITE_CODE
    await q.edit_message_text(
        f"✅ Selected: *{esc(platform.upper())}*\n"
        f"Invite: `{esc(context.user_data['invite'])}`\n\n"
        "📱 Enter your mobile number \\(10\\-15 digits\\):",
        parse_mode="MarkdownV2",
        reply_markup=back_keyboard(),
    )
    return MOBILE


async def mobile_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mobile = update.message.text.strip()
    # BUG FIX: original regex was r"^d{10,15}$" (missing backslash), so it
    # never matched a real phone number and every registration was rejected.
    if not re.match(r"^\d{10,15}$", mobile):
        await update.message.reply_text("❌ Invalid. Enter 10-15 digits:", reply_markup=back_keyboard())
        return MOBILE

    context.user_data["mobile"] = mobile
    platform = context.user_data["platform"]

    try:
        if platform == "holwin":
            async with HolwinClient() as client:
                resp = await client.post("/api/system/sms/send", {"mobile": mobile, "type": "reg_code"})
        else:
            async with RexClient() as client:
                resp = await client.post("/app/user/sendSmsCode", {"mobileNo": mobile})
    except Exception as e:
        logger.error(f"OTP send error: {e}")
        await update.message.reply_text("❌ Failed to send OTP.", reply_markup=back_keyboard())
        return ConversationHandler.END

    ok = (platform == "holwin" and resp.get("code") == 0) or (platform == "rex" and resp.get("code") == 200)
    if not ok:
        await update.message.reply_text(
            f"❌ OTP request failed: {resp.get('msg', 'Unknown')}",
            reply_markup=back_keyboard(),
        )
        return ConversationHandler.END

    await update.message.reply_text("✅ OTP sent! Enter the OTP:", reply_markup=otp_keyboard())
    return OTP


async def otp_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp_code = update.message.text.strip()
    if not otp_code.isdigit():
        await update.message.reply_text("❌ OTP must be numeric. Try again:", reply_markup=otp_keyboard())
        return OTP
    context.user_data["otp"] = otp_code
    await update.message.reply_text("🔑 Set a password, or type `skip`:", parse_mode="MarkdownV2")
    return PASSWORD


async def password_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pwd = update.message.text.strip()
    platform = context.user_data["platform"]

    if pwd.lower() == "skip":
        pwd = "Dk12345dk" if platform == "rex" else "Password@123"
    elif len(pwd) < 6:
        await update.message.reply_text("❌ Min 6 characters. Try again or type `skip`:")
        return PASSWORD

    context.user_data["password"] = pwd
    mobile = context.user_data["mobile"]
    invite = context.user_data["invite"]
    summary = (
        "📋 *Summary*\n\n"
        f"📱 Mobile: `{esc(mobile)}`\n"
        f"🔑 Password: `{'*' * len(pwd)}`\n"
        f"🎫 Platform: `{esc(platform.upper())}`\n"
        f"🎫 Invite: `{esc(invite)}`\n\n"
        "Confirm?"
    )
    await update.message.reply_text(summary, parse_mode="MarkdownV2", reply_markup=confirm_keyboard())
    return CONFIRM


async def resend_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Resending OTP...")
    mobile = context.user_data.get("mobile")
    platform = context.user_data.get("platform")
    if not mobile or not platform:
        await q.edit_message_text("❌ Session expired. Use /start again.")
        return ConversationHandler.END

    try:
        if platform == "holwin":
            async with HolwinClient() as client:
                resp = await client.post("/api/system/sms/send", {"mobile": mobile, "type": "reg_code"})
        else:
            async with RexClient() as client:
                resp = await client.post("/app/user/sendSmsCode", {"mobileNo": mobile})
    except Exception as e:
        logger.error(f"Resend OTP error: {e}")
        await q.edit_message_text("❌ Failed to resend OTP.")
        return ConversationHandler.END

    ok = (platform == "holwin" and resp.get("code") == 0) or (platform == "rex" and resp.get("code") == 200)
    if not ok:
        await q.edit_message_text(f"❌ Resend failed: {resp.get('msg', 'Unknown')}")
        return ConversationHandler.END

    await q.edit_message_text("✅ OTP resent successfully. Enter OTP:", reply_markup=otp_keyboard())
    return OTP


async def change_mobile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("✏️ Enter your new mobile number (10-15 digits):", reply_markup=back_keyboard())
    return MOBILE


async def confirm_reg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    platform = context.user_data.get("platform")
    mobile = context.user_data.get("mobile")
    otp_code = context.user_data.get("otp")
    password = context.user_data.get("password")
    invite = context.user_data.get("invite")

    if not all([platform, mobile, otp_code, password, invite]):
        await q.edit_message_text("❌ Session expired. Use /start again.")
        return ConversationHandler.END

    try:
        if platform == "holwin":
            async with HolwinClient() as client:
                payload = {
                    "mobile": mobile,
                    "authCode": otp_code,
                    "password": password,
                    "inviteCode": invite,
                    "sourceAppType": "lobby",
                    "registerHost": "www.holwin123.top",
                    "sourceUrl": "https://www.hlowin.link/",
                }
                resp = await client.post("/api/user/register", payload)
                success = resp.get("code") == 0
        else:
            async with RexClient() as client:
                payload = {
                    "mobileNo": mobile,
                    "password": password,
                    "smsCode": otp_code,
                    "inviteCode": invite,
                }
                resp = await client.post("/app/user/register", payload)
                success = resp.get("code") == 200
    except Exception as e:
        logger.error(f"Registration error: {e}")
        await q.edit_message_text("❌ Registration failed due to network error.")
        return ConversationHandler.END

    if success:
        try:
            save_registration(mobile, platform, invite, update.effective_user.id)
        except Exception:
            await q.edit_message_text("❌ Registration succeeded but local save failed.")
            return ConversationHandler.END

        await q.edit_message_text(
            "✅ *Registration successful\\!*\n\n"
            f"Platform: {esc(platform.upper())}\n"
            f"Mobile: `{esc(mobile)}`\n"
            f"Invite used: `{esc(invite)}`\n\n"
            "Saved locally\\.",
            parse_mode="MarkdownV2",
            reply_markup=back_keyboard(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    await q.edit_message_text(
        f"❌ Registration failed: `{esc(resp.get('msg', 'Unknown error'))}`",
        parse_mode="MarkdownV2",
        reply_markup=back_keyboard(),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Cancelled.")
    else:
        await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception while processing an update", exc_info=context.error)


conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(platform_selected, pattern="^platform_(holwin|rex)$")],
    states={
        MOBILE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, mobile_input),
            CallbackQueryHandler(main_menu, pattern="^main_menu$"),
        ],
        OTP: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, otp_input),
            CallbackQueryHandler(resend_otp, pattern="^resend_otp$"),
            CallbackQueryHandler(change_mobile, pattern="^change_mobile$"),
            CallbackQueryHandler(main_menu, pattern="^main_menu$"),
        ],
        PASSWORD: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, password_input),
            CallbackQueryHandler(main_menu, pattern="^main_menu$"),
        ],
        CONFIRM: [
            CallbackQueryHandler(confirm_reg, pattern="^confirm_reg$"),
            CallbackQueryHandler(change_mobile, pattern="^change_mobile$"),
            CallbackQueryHandler(cancel, pattern="^cancel_reg$"),
            CallbackQueryHandler(main_menu, pattern="^main_menu$"),
        ],
    },
    fallbacks=[
        CommandHandler("cancel", cancel),
        CallbackQueryHandler(main_menu, pattern="^main_menu$"),
    ],
    allow_reentry=True,
)


def main():
    if not BOT_TOKEN or BOT_TOKEN.count(":") != 1:
        raise SystemExit("BOT_TOKEN is missing or malformed. Set it via the BOT_TOKEN environment variable.")

    app = Application.builder().token(BOT_TOKEN).concurrent_updates(False).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("my", my_cmd))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(conv_handler)

    app.add_handler(CallbackQueryHandler(stats_cmd, pattern="^stats_btn$"))
    app.add_handler(CallbackQueryHandler(my_cmd, pattern="^my_btn$"))
    app.add_handler(CallbackQueryHandler(help_cmd, pattern="^help_btn$"))
    app.add_handler(CallbackQueryHandler(main_menu, pattern="^main_menu$"))

    app.add_error_handler(error_handler)

    logger.info("Bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
