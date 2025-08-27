import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from typing import Optional, Dict

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ======== HARD-CODED TOKEN & DEFAULT CHAT ID ========
TOKEN = "8179378309:AAGbscsJJ0ScMKEna_j-2kVfrcx0TL8Mn80"
DEFAULT_CHAT_ID = -4803745789  # your group chat ID

# Pin polls? (True/False)
PIN_POLLS = True  # keep pinning enabled

SGT = ZoneInfo("Asia/Singapore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------- Poll tracking (stores chat_id + message_id) ----------
@dataclass
class PollRef:
    chat_id: int
    message_id: int

# In-memory storage (resets if container restarts)
STATE: Dict[str, Optional[PollRef]] = {"cg_poll": None, "svc_poll": None}

# ---------- Date helpers ----------
def next_weekday_date_exclusive(now_dt: datetime, weekday: int):
    """Next weekday strictly after today (Sun->Fri poll scheduling)."""
    days_ahead = (weekday - now_dt.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (now_dt + timedelta(days=days_ahead)).date()

def next_or_same_weekday_date(now_dt: datetime, weekday: int):
    """Today if it's the weekday, else the next one (for Fri/Sun reminders)."""
    days_ahead = (weekday - now_dt.weekday()) % 7
    return (now_dt + timedelta(days=days_ahead)).date()

def upcoming_friday_for_poll(now_dt: datetime): 
    return next_weekday_date_exclusive(now_dt, 4)  # Fri = 4

def upcoming_sunday_for_poll(now_dt: datetime): 
    return next_weekday_date_exclusive(now_dt, 6)  # Sun = 6

def friday_for_reminder(now_dt: datetime):
    return next_or_same_weekday_date(now_dt, 4)

def sunday_for_reminder(now_dt: datetime):
    return next_or_same_weekday_date(now_dt, 6)

def ordinal(n: int) -> str:
    # 1st, 2nd, 3rd, 4th ... with 11th–13th exception
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"

def format_date_long(d) -> str:
    # e.g., 31st August 2025 (Sun)
    return f"{ordinal(d.day)} {d.strftime('%B %Y')} ({d.strftime('%a')})"

def format_date_plain(d) -> str:
    # e.g., 31st August 2025
    return f"{ordinal(d.day)} {d.strftime('%B %Y')}"

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
    text_plain: str,
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
    target_chat = _effective_target_chat(update)
    now = datetime.now(SGT)
    target = upcoming_sunday_for_poll(now)
    msg = await ctx.bot.send_poll(
        chat_id=target_chat,
        question=f"Sunday Service – {format_date_long(target)}",
        options=["9am", "11.15am", "Serving", "Lunch", "Invited a friend"],
        is_anonymous=False,
        allows_multiple_answers=True,
    )
    STATE["svc_poll"] = PollRef(chat_id=target_chat, message_id=msg.message_id)
    await _safe_pin(ctx, target_chat, msg.message_id)

async def send_cell_group_poll(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None):
    target_chat = _effective_target_chat(update)
    now = datetime.now(SGT)
    target = upcoming_friday_for_poll(now)
    msg = await ctx.bot.send_poll(
        chat_id=target_chat,
        question=f"Cell Group – {format_date_long(target)}",
        options=["Dinner 7.15pm", "CG 8.15pm", "Cannot make it"],
        is_anonymous=False,
        allows_multiple_answers=False,
    )
    STATE["cg_poll"] = PollRef(chat_id=target_chat, message_id=msg.message_id)
    await _safe_pin(ctx, target_chat, msg.message_id)

# ---------- Reminders (chat-aware + date-specific + fallback) ----------
async def remind_sunday_service(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None):
    now = datetime.now(SGT)
    target_date = sunday_for_reminder(now)
    date_txt = format_date_plain(target_date)

    ref = STATE.get("svc_poll")
    text_reply = f"⏰ Reminder: Please vote on the Sunday Service poll above for {date_txt}."
    text_plain = f"⏰ Reminder: Please vote on the Sunday Service poll for {date_txt}."
    if ref:
        await _remind_with_reply_fallback(
            ctx,
            target_chat_id=ref.chat_id,
            reply_to_msg_id=ref.message_id,
            text_reply=text_reply,
            text_plain=text_plain,
        )
    else:
        target_chat = _effective_target_chat(update)
        await _remind_with_reply_fallback(
            ctx,
            target_chat_id=target_chat,
            reply_to_msg_id=None,
            text_reply="",
            text_plain=text_plain,
        )

async def remind_cell_group(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None):
    now = datetime.now(SGT)
    target_date = friday_for_reminder(now)  # inclusive for Friday reminders
    date_txt = format_date_plain(target_date)

    ref = STATE.get("cg_poll")
    text_reply = f"⏰ Reminder: Please vote on the Cell Group poll above for {date_txt}."
    text_plain = f"⏰ Reminder: Please vote on the Cell Group poll for {date_txt}."
    if ref:
