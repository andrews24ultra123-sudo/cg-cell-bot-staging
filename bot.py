import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from typing import Optional, Dict

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ======== HARD-CODED TOKEN & DEFAULT CHAT ID ========
TOKEN = "8179378309:AAGbscsJJ0ScMKEna_j-2kVfrcx0TL8Mn80"
# This is only used if you manually run commands in a different chat.
DEFAULT_CHAT_ID = 54380770  # personal chat ID

# Pin polls? (True/False)
PIN_POLLS = False

SGT = ZoneInfo("Asia/Singapore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------- Poll tracking (now stores chat_id + message_id) ----------
@dataclass
class PollRef:
    chat_id: int
    message_id: int

# In-memory storage (resets if container restarts)
STATE: Dict[str, Optional[PollRef]] = {"cg_poll": None, "svc_poll": None}

# ---------- Helpers ----------
def next_weekday_date(now_dt: datetime, weekday: int):
    days_ahead = (weekday - now_dt.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (now_dt + timedelta(days=days_ahead)).date()

def upcoming_friday(now_dt: datetime): 
    return next_weekday_date(now_dt, 4)

def upcoming_sunday(now_dt: datetime): 
    return next_weekday_date(now_dt, 6)

def _effective_target_chat(update: Optional[Update]) -> int:
    """
    If a command is run in some chat, prefer replying in that chat.
    Otherwise fall back to DEFAULT_CHAT_ID.
    """
    if update and update.effective_chat:
        return update.effective_chat.id
    return DEFAULT_CHAT_ID

async def _safe_pin(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    if not PIN_POLLS:
        return
    try:
        await ctx.bot.pin_chat_message(chat_id, message_id, disable_notification=True)
    except Exception as e:
        logging.warning(f"Pin failed: {e}")

async def _remind_with_reply_fallback(
    ctx: ContextTypes.DEFAULT_TYPE, 
    target_chat_id: int, 
    reply_to_msg_id: Optional[int], 
    text_reply: str, 
    text_plain: str
):
    """
    Try to reply to the original poll; if it fails (wrong chat / deleted), 
    send a plain reminder to the target chat.
    """
    try:
        if reply_to_msg_id:
            await ctx.bot.send_message(
                chat_id=target_chat_id,
                text=text_reply,
                reply_to_message_id=reply_to_msg_id,
                allow_sending_without_reply=True,
            )
        else:
            await ctx.bot.send_message(chat_id=target_chat_id, text=text_plain)
    except Exception as e:
        logging.warning(f"Reply failed ({e}); sending plain reminder instead.")
        try:
            await ctx.bot.send_message(chat_id=target_chat_id, text=text_plain)
        except Exception as e2:
            logging.exception(f"Plain reminder also failed: {e2}")

# ---------- Poll senders ----------
async def send_sunday_service_poll(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None):
    # Post poll in the chat where command was issued; else default
    target_chat = _effective_target_chat(update)
    now = datetime.now(SGT)
    target = upcoming_sunday(now)
    msg = await ctx.bot.send_poll(
        chat_id=target_chat,
        question=f"Sunday Service – {target:%Y-%m-%d (%a)}",
        options=["9am", "11.15am", "Serving", "Lunch", "Invited a friend"],
        is_anonymous=False,
        allows_multiple_answers=True,
    )
    STATE["svc_poll"] = PollRef(chat_id=target_chat, message_id=msg.message_id)
    await _safe_pin(ctx, target_chat, msg.message_id)

async def send_cell_group_poll(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None):
    target_chat = _effective_target_chat(update)
    now = datetime.now(SGT)
    target = upcoming_friday(now)
    msg = await ctx.bot.send_poll(
        chat_id=target_chat,
        question=f"Cell Group – {target:%Y-%m-%d (%a)}",
        options=["Dinner 7.15pm", "CG 8.15pm", "Cannot make it"],
        is_anonymous=False,
        allows_multiple_answers=False,
    )
    STATE["cg_poll"] = PollRef(chat_id=target_chat, message_id=msg.message_id)
    await _safe_pin(ctx, target_chat, msg.message_id)

# ---------- Reminders (chat-aware + fallback) ----------
async def remind_sunday_service(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None):
    ref = STATE.get("svc_poll")
    if ref:
        await _remind_with_reply_fallback(
            ctx,
            target_chat_id=ref.chat_id,
            reply_to_msg_id=ref.message_id,
            text_reply="⏰ Reminder: Please vote on the Sunday Service poll above 🙏",
            text_plain="⏰ Reminder: Please vote on the Sunday Service poll.",
        )
    else:
        # No saved poll; send to the chat where command came from or default
        target_chat = _effective_target_chat(update)
        await _remind_with_reply_fallback(
            ctx,
            target_chat_id=target_chat,
            reply_to_msg_id=None,
            text_reply="",
            text_plain="⏰ Reminder: Please vote on the Sunday Service poll.",
        )

async def remind_cell_group(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None):
    ref = STATE.get("cg_poll")
    if ref:
        await _remind_with_reply_fallback(
            ctx,
            target_chat_id=ref.chat_id,
            reply_to_msg_id=ref.message_id,
            text_reply="⏰ Reminder: Please vote on the Cell Group poll above 👆",
            text_plain="⏰ Reminder: Please vote on the Cell Group poll.",
        )
    else:
        target_chat = _effective_target_chat(update)
        await _remind_with_reply_fallback(
            ctx,
            target_chat_id=target_chat,
            reply_to_msg_id=None,
            text_reply="",
            text_plain="⏰ Reminder: Please vote on the Cell Group poll.",
        )

# ---------- Commands ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Schedule (SGT):\n"
        "• Cell Group (Friday):\n"
        "  - Sun 6:00 PM → post poll\n"
        "  - Mon 6:00 PM → reminder\n"
        "  - Thu 6:00 PM → reminder\n"
        "  - Fri 3:00 PM → reminder\n"
        "• Sunday Service:\n"
        "  - Fri 11:30 PM → post poll\n"
        "  - Sat 12:00 PM → reminder\n\n"
        "Manual commands:\n"
        "/cgpoll → post CG poll (posts in the chat you send this from)\n"
        "/cgrm → reminder for last CG poll\n"
        "/sunpoll → post Service poll (posts in the chat you send this from)\n"
        "/sunrm → reminder for last Service poll\n"
        "/testpoll → test poll\n"
        "/id → show chat id"
    )

async def cgpoll_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_cell_group_poll(ctx, update)

async def cgrm_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await remind_cell_group(ctx, update)

async def sunpoll_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_sunday_service_poll(ctx, update)

async def sunrm_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await remind_sunday_service(ctx, update)

async def testpoll_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target_chat = _effective_target_chat(update)
    awa
