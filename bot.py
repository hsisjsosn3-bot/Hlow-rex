# bot_improved.py
# -*- coding: utf-8 -*-
import os
import re
import logging
from datetime import datetime
from typing import Dict, Any
import asyncio # Import asyncio for async sleep/delays
import aiohttp
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Index, func
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.exc import SQLAlchemyError # Import SQLAlchemy specific errors
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

# --- Configuration ---
# Load sensitive data from environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required.")

# Load database URL from environment variable (allows easy switch to Postgres)
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///registrations.db")

# Load invite codes and API details from environment variables
HOLWIN_INVITE_CODE = os.environ.get("HOLWIN_INVITE_CODE", "WLRPSY") 
REX_INVITE_CODE = os.environ.get("REX_INVITE_CODE", "O6NVYX")
HOLWIN_BASE = os.environ.get("HOLWIN_BASE", "https://www.holwin123.top")
HOLWIN_DI = os.environ.get("HOLWIN_DI", "88dd52c70e7b377527be01c39f5a0a4f")
HOLWIN_VTOKEN = os.environ.get("HOLWIN_VTOKEN", "18667bd921478af5fe5f6506865e4f8a")
REX_BASE = os.environ.get("REX_BASE", "https://rcapi.rexproearn.com")
REX_ORIGIN = os.environ.get("REX_ORIGIN", "https://rch5.rexproearn.com") # Added for header
REX_REFERER = os.environ.get("REX_REFERER", "https://rch5.rexproearn.com/") # Added for header

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO, # Consider using os.environ.get("LOG_LEVEL", "INFO") for flexibility
)
logger = logging.getLogger(__name__)

# --- Database Setup ---
# Render's PostgreSQL uses 'postgresql://' not 'postgres://'. Handle both.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"timeout": 30}, # Timeout for SQLite locks
    pool_pre_ping=True, # Validates connections before use (good for Render)
    echo=False # Set to True only for debugging SQL queries
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
        Index('idx_platform', 'platform'),
        Index('idx_telegram_id', 'telegram_id'),
        Index('idx_registered_at', 'registered_at'),
    )

Base.metadata.create_all(bind=engine) # Create tables using the engine

# --- Conversation States ---
MOBILE, OTP, PASSWORD, CONFIRM = range(4)

# --- Utility Functions ---
_MDV2_SPECIAL = re.compile(r'([_*\[\]()~`>#+\-=|{}.!\\])') # Corrected regex for MarkdownV2 escaping
def esc(text: str) -> str:
    """Escapes characters for Telegram MarkdownV2."""
    return _MDV2_SPECIAL.sub(r'\\\1', str(text))

# --- Keyboard Layouts ---
def main_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🏠 Holwin", callback_data="platform_holwin"),
            InlineKeyboardButton("📈 Rexproearn", callback_data="platform_rex"),
        ],
        [
            InlineKeyboardButton("📊 Stats", callback_data="stats_btn"),
            InlineKeyboardButton("📋 My Registrations", callback_data="my_btn"),
            InlineKeyboardButton("❓ Help", callback_data="help_btn"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def back_keyboard():
    keyboard = [[InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]]
    return InlineKeyboardMarkup(keyboard)

def otp_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔄 Resend OTP", callback_data="resend_otp")],
        [InlineKeyboardButton("✏️ Change Mobile", callback_data="change_mobile")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_reg_process")], # Changed callback
    ]
    return InlineKeyboardMarkup(keyboard)

def confirm_keyboard():
    keyboard = [
        [InlineKeyboardButton("✅ Confirm", callback_data="confirm_reg")],
        [InlineKeyboardButton("✏️ Change Mobile", callback_data="change_mobile")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_reg_process")], # Changed callback
    ]
    return InlineKeyboardMarkup(keyboard)

def db_session():
    """Provides a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def save_registration(mobile: str, platform: str, invite: str, telegram_id: int):
    """Saves registration data to the database."""
    try:
        db = next(db_session())
        reg = Registration(
            mobile=mobile,
            platform=platform,
            invite_used=invite,
            telegram_id=telegram_id
        )
        db.add(reg)
        db.commit()
        db.refresh(reg) # Optional: get the ID back
        logger.info(f"Saved registration for mobile {mobile} on {platform}.")
        return True
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database save error: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error saving registration: {e}")
        return False
    finally:
        db.close()

def get_stats():
    """Retrieves global statistics."""
    try:
        db = next(db_session())
        total = db.query(func.count(Registration.id)).scalar() or 0
        holwin_count = db.query(func.count(Registration.id)).filter(Registration.platform == "holwin").scalar() or 0
        rex_count = db.query(func.count(Registration.id)).filter(Registration.platform == "rex").scalar() or 0
        recent = db.query(Registration).order_by(Registration.registered_at.desc()).limit(10).all()
        return total, holwin_count, rex_count, recent
    except SQLAlchemyError as e:
        logger.error(f"Database fetch error: {e}")
        return 0, 0, 0, []
    finally:
        db.close()

def get_user_stats(user_id: int):
    """Retrieves statistics for a specific user."""
    try:
        db = next(db_session())
        total = db.query(func.count(Registration.id)).filter(Registration.telegram_id == user_id).scalar() or 0
        holwin_count = db.query(func.count(Registration.id)).filter(
            Registration.telegram_id == user_id,
            Registration.platform == "holwin"
        ).scalar() or 0
        rex_count = db.query(func.count(Registration.id)).filter(
            Registration.telegram_id == user_id,
            Registration.platform == "rex"
        ).scalar() or 0
        return total, holwin_count, rex_count
    except SQLAlchemyError as e:
        logger.error(f"Database fetch error for user {user_id}: {e}")
        return 0, 0, 0
    finally:
        db.close()

# --- API Clients ---
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

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with self.session.post(f"{HOLWIN_BASE}{path}", json=payload) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception as e:
                    logger.error(f"Holwin non-JSON response ({resp.status}): {e}")
                    return {"code": -1, "msg": f"Invalid response from server (HTTP {resp.status})"}
                if not isinstance(data, dict):
                    return {"code": -1, "msg": "Unexpected response format"}
                return data
        except aiohttp.ClientConnectorError as e:
            logger.error(f"Holwin connection error: {e}")
            return {"code": -1, "msg": "Connection failed to Holwin server."}
        except asyncio.TimeoutError:
            logger.error("Holwin request timed out.")
            return {"code": -1, "msg": "Request timed out."}
        except Exception as e:
            logger.error(f"Holwin general error: {e}")
            return {"code": -1, "msg": "An unexpected error occurred."}


class RexClient:
    def __init__(self):
        self.session = None

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=20)
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": REX_ORIGIN, # Use env var
            "Referer": REX_REFERER, # Use env var
        }
        self.session = aiohttp.ClientSession(headers=headers, timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with self.session.post(f"{REX_BASE}{path}", json=payload) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception as e:
                    logger.error(f"Rex non-JSON response ({resp.status}): {e}")
                    return {"code": -1, "msg": f"Invalid response from server (HTTP {resp.status})"}
                if not isinstance(data, dict):
                    return {"code": -1, "msg": "Unexpected response format"}
                return data
        except aiohttp.ClientConnectorError as e:
            logger.error(f"Rex connection error: {e}")
            return {"code": -1, "msg": "Connection failed to Rex server."}
        except asyncio.TimeoutError:
            logger.error("Rex request timed out.")
            return {"code": -1, "msg": "Request timed out."}
        except Exception as e:
            logger.error(f"Rex general error: {e}")
            return {"code": -1, "msg": "An unexpected error occurred."}

# --- Handler Functions ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command and returns to main menu."""
    context.user_data.clear()
    msg = (
        "╔═══════════════════════════════════════╗\n"
        "║     💎  R E F E R R A L  B O T       ║\n"
        "╚═══════════════════════════════════════╝\n\n"
        "🚀  Select your battle-ground: \n\n"
        "┌─────────────────────────────┐\n"
        f"│  🏠  Holwin                 │\n"
        f"│  Invite:  `{esc(HOLWIN_INVITE_CODE)}`    │\n"
        "├─────────────────────────────┤\n"
        f"│  📈  Rexproearn             │\n"
        f"│  Invite:  `{esc(REX_INVITE_CODE)}`       │\n"
        "└─────────────────────────────┘\n\n"
        "🛡️  Features: \n"
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
    """Handles /help command."""
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
    """Handles /stats command and stats button."""
    total, holwin, rex, recent = get_stats()
    msg = (
        "📊  Global Stats \n\n"
        f"👥 Total:  `{total}` \n"
        f"🏠 Holwin:  `{holwin}` \n"
        f"📈 Rexproearn:  `{rex}` \n\n"
        "🕒  Last 10 Registrations: \n"
    )
    if recent:
        for r in recent:
            msg += f"•  `{esc(r.mobile)}`  \\- {esc(r.platform.upper())} \\- {esc(r.registered_at.strftime('%Y-%m-%d %H:%M'))}\n"
    else:
        msg += "No registrations yet\\. "

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
    """Handles /my command and my registrations button."""
    total, holwin, rex = get_user_stats(update.effective_user.id)
    msg = (
        "📋  Your Registrations \n\n"
        f"👤 Total:  `{total}` \n"
        f"🏠 Holwin:  `{holwin}` \n"
        f"📈 Rexproearn:  `{rex}` "
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, parse_mode="MarkdownV2", reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode="MarkdownV2", reply_markup=kb)

async def platform_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles platform selection."""
    query = update.callback_query
    await query.answer()
    platform = query.data.split("_")[1]
    context.user_data["platform"] = platform
    context.user_data["invite"] = HOLWIN_INVITE_CODE if platform == "holwin" else REX_INVITE_CODE

    await query.edit_message_text(
        f"✅ Selected:  {esc(platform.upper())} \n"
        f"Invite:  `{esc(context.user_data['invite'])}` \n\n"
        "📱 Enter your mobile number (10-15 digits): ",
        parse_mode="MarkdownV2",
        reply_markup=back_keyboard(),
    )
    return MOBILE

async def mobile_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles mobile number input."""
    mobile = update.message.text.strip()
    if not re.match(r"^\d{10,15}$", mobile):
        await update.message.reply_text("❌ Invalid. Enter 10-15 digits:", reply_markup=back_keyboard())
        return MOBILE

    context.user_data["mobile"] = mobile
    platform = context.user_data["platform"]

    # Show a temporary message while sending OTP
    temp_msg = await update.message.reply_text("⏳ Sending OTP... Please wait.", reply_markup=otp_keyboard())

    try:
        if platform == "holwin":
            async with HolwinClient() as client:
                resp = await client.post("/api/system/sms/send", {"mobile": mobile, "type": "reg_code"})
        else:
            async with RexClient() as client:
                resp = await client.post("/app/user/sendSmsCode", {"mobileNo": mobile})

        ok = (platform == "holwin" and resp.get("code") == 0) or (platform == "rex" and resp.get("code") == 200)

        if not ok:
            # Edit the temporary message with the error
            await temp_msg.edit_text(
                f"❌ OTP request failed: {resp.get('msg', 'Unknown error')}",
                reply_markup=otp_keyboard()
            )
            return OTP # Stay in OTP state to allow retry/resend

        # Edit the temporary message with success prompt
        await temp_msg.edit_text("✅ OTP sent! Enter the OTP:", reply_markup=otp_keyboard())
        return OTP

    except Exception as e:
        logger.error(f"OTP send error: {e}")
        # Edit the temporary message with the error
        await temp_msg.edit_text("❌ Failed to send OTP. Check logs or try again.", reply_markup=otp_keyboard())
        return OTP # Stay in OTP state to allow retry/resend

async def otp_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles OTP input."""
    otp_code = update.message.text.strip()
    if not otp_code.isdigit():
        await update.message.reply_text("❌ OTP must be numeric. Try again:", reply_markup=otp_keyboard())
        return OTP
    context.user_data["otp"] = otp_code
    await update.message.reply_text("🔑 Set a password (min 6 chars), or type `skip`:", parse_mode="MarkdownV2")
    return PASSWORD

async def password_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles password input."""
    pwd = update.message.text.strip()
    platform = context.user_data["platform"]

    if pwd.lower() == "skip":
        pwd = "Dk12345dk" if platform == "rex" else "Password@123"
    elif len(pwd) < 6:
        await update.message.reply_text("❌ Min 6 characters. Try again or type `skip`:", parse_mode="MarkdownV2")
        return PASSWORD

    context.user_data["password"] = pwd
    mobile = context.user_data["mobile"]
    invite = context.user_data["invite"]

    summary = (
        "📋 *Summary*\n\n"
        f"📱 Mobile: `{esc(mobile)}`\n"
        f"🔑 Password: `{'*' * len(pwd)}`\n" # Show masked password
        f"🎫 Platform: `{esc(platform.upper())}`\n"
        f"🎫 Invite: `{esc(invite)}`\n\n"
        "Confirm?"
    )
    await update.message.reply_text(summary, parse_mode="MarkdownV2", reply_markup=confirm_keyboard())
    return CONFIRM

async def resend_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles OTP resend button."""
    query = update.callback_query
    await query.answer("Resending OTP...")

    mobile = context.user_data.get("mobile")
    platform = context.user_data.get("platform")
    if not mobile or not platform:
        await query.edit_message_text("❌ Session expired. Use /start again.")
        return ConversationHandler.END

    # Show a temporary message while resending OTP
    await query.edit_message_text("⏳ Resending OTP... Please wait.", reply_markup=otp_keyboard())

    try:
        if platform == "holwin":
            async with HolwinClient() as client:
                resp = await client.post("/api/system/sms/send", {"mobile": mobile, "type": "reg_code"})
        else:
            async with RexClient() as client:
                resp = await client.post("/app/user/sendSmsCode", {"mobileNo": mobile})

        ok = (platform == "holwin" and resp.get("code") == 0) or (platform == "rex" and resp.get("code") == 200)

        if not ok:
            await query.edit_message_text(f"❌ Resend failed: {resp.get('msg', 'Unknown error')}", reply_markup=otp_keyboard())
            return OTP

        await query.edit_message_text("✅ OTP resent successfully. Enter OTP:", reply_markup=otp_keyboard())
        return OTP

    except Exception as e:
        logger.error(f"Resend OTP error: {e}")
        await query.edit_message_text("❌ Failed to resend OTP. Check logs or try again.", reply_markup=otp_keyboard())
        return OTP

async def change_mobile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles changing the mobile number."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✏️ Enter your new mobile number (10-15 digits):", reply_markup=back_keyboard())
    return MOBILE

async def confirm_reg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles final confirmation and registration."""
    query = update.callback_query
    await query.answer()

    platform = context.user_data.get("platform")
    mobile = context.user_data.get("mobile")
    otp_code = context.user_data.get("otp")
    password = context.user_data.get("password")
    invite = context.user_data.get("invite")

    if not all([platform, mobile, otp_code, password, invite]):
        await query.edit_message_text("❌ Session expired. Use /start again.")
        return ConversationHandler.END

    # Show a temporary message while registering
    await query.edit_message_text("⏳ Registering... Please wait.", reply_markup=InlineKeyboardMarkup([]))

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

        if success:
            # Attempt to save registration locally
            save_success = save_registration(mobile, platform, invite, update.effective_user.id)
            if save_success:
                await query.edit_message_text(
                    "✅ *Registration successful!*\n\n"
                    f"Platform: {esc(platform.upper())}\n"
                    f"Mobile: `{esc(mobile)}`\n"
                    f"Invite used: `{esc(invite)}`\n\n"
                    "Saved locally.",
                    parse_mode="MarkdownV2",
                    reply_markup=back_keyboard(),
                )
            else:
                await query.edit_message_text(
                    "⚠️ Registration succeeded on the platform, but failed to save locally. Contact admin.",
                    parse_mode="MarkdownV2",
                    reply_markup=back_keyboard(),
                )
            context.user_data.clear() # Clear state after successful/failed save attempt
            return ConversationHandler.END
        else:
            await query.edit_message_text(
                f"❌ Registration failed on platform: `{esc(resp.get('msg', 'Unknown error'))}`",
                parse_mode="MarkdownV2",
                reply_markup=back_keyboard(),
            )
            return ConversationHandler.END

    except Exception as e:
        logger.error(f"Registration error: {e}")
        await query.edit_message_text(
            "❌ Registration failed due to a network or system error.",
            parse_mode="MarkdownV2",
            reply_markup=back_keyboard(),
        )
        context.user_data.clear() # Clear state on exception
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /cancel command."""
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Process cancelled.")
    else:
        await update.message.reply_text("❌ Process cancelled.")
    return ConversationHandler.END

# Specific handler for cancel buttons within conversation
async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles cancel button clicks during registration flow."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Registration process cancelled.")
    context.user_data.clear()
    return ConversationHandler.END

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Returns to the main menu."""
    await start(update, context)
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Logs errors caused by updates."""
    logger.error(f"Update {update.update_id} caused error: {context.error}")

# --- Main Application Setup ---
def main():
    """Starts the bot."""
    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(platform_selected, pattern=r"^platform_(holwin|rex)$")],
        states={
            MOBILE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mobile_input),
                CallbackQueryHandler(main_menu, pattern=r"^main_menu$"),
            ],
            OTP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, otp_input),
                CallbackQueryHandler(resend_otp, pattern=r"^resend_otp$"),
                CallbackQueryHandler(change_mobile, pattern=r"^change_mobile$"),
                CallbackQueryHandler(main_menu, pattern=r"^main_menu$"), # Allow going back from OTP too
            ],
            PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, password_input),
                CallbackQueryHandler(main_menu, pattern=r"^main_menu$"),
            ],
            CONFIRM: [
                CallbackQueryHandler(confirm_reg, pattern=r"^confirm_reg$"),
                CallbackQueryHandler(change_mobile, pattern=r"^change_mobile$"),
                CallbackQueryHandler(cancel_conversation, pattern=r"^cancel_reg_process$"), # New specific handler
                CallbackQueryHandler(main_menu, pattern=r"^main_menu$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(main_menu, pattern=r"^main_menu$"),
        ],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("my", my_cmd))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(stats_cmd, pattern=r"^stats_btn$"))
    application.add_handler(CallbackQueryHandler(my_cmd, pattern=r"^my_btn$"))
    application.add_handler(CallbackQueryHandler(help_cmd, pattern=r"^help_btn$"))
    application.add_handler(CallbackQueryHandler(main_menu, pattern=r"^main_menu$"))
    application.add_error_handler(error_handler)

    logger.info("Bot started...")

    # Use webhook if WEBHOOK_URL is set, otherwise polling
    webhook_url = os.environ.get("WEBHOOK_URL")
    if webhook_url:
        port = int(os.environ.get("PORT", 8443)) # Render sets PORT
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,
            webhook_url=f"{webhook_url}/{BOT_TOKEN}"
        )
    else:
        application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
