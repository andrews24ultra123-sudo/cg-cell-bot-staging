import os, json, logging
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Optional, Dict, Tuple

# ---- Telegram imports (and version log) ----
import telegram
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes

# Try to import Days enum (PTB v20+). Fallback to integers if unavailable.
try:
    from telegram.ext import Days  # Days.MONDAY ... Days.SUNDAY
except Exception:
    class Days:
        MONDAY = 0
        TUESDAY = 1
        WEDNESDAY = 2
        THURSDAY = 3
        FRIDAY = 4
        SATURDAY = 5
        SUNDAY = 6

# ======== HARD-CODED TOKEN & DEFAULT CHAT ID ========
TOKEN = "8179378309:AAGbscsJJ0ScMKEna_j-2kVfrcx0TL8Mn80"
DEFAULT_CHAT_ID = -4911009091  # your group chat ID

# Pin polls? (True/False)
PIN_POLLS = True  # keep pinning enabled

# Persist last poll IDs so reminders can reply even after a restart
STATE_PATH = "./state.json"

# ---- Timezone: ZoneInfo with pytz fallback ----
try:
    from zoneinfo import ZoneInfo
    SGT = ZoneInfo("Asia/Singapore")
except Exception:
    try:
        import pytz  # type: ignore
        SGT = pytz.timezone("Asia/Singapore")
    except Exception:
        from datetime import timezone as _tz
        SGT = _tz.utc  # last resort; use UTC

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.info(f"python-telegram-bot version: {getattr(telegram, '__version__', 'unknown')}")

# ---------- Poll tracking (stores chat_id + message_id) ----------
@dataclass
class PollRef:
    chat_id: int
    message_id: int

# In-memory storage (resets if container restarts)
STATE: Dict[str, Optional[PollRef]] = {"cg_poll": None, "svc_poll": None}

def _load_state() -> None:
    global STATE
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for k in ("cg_poll", "svc_poll"):
                v = raw.get(k)
                if v and isinstance(v, dict) and "chat_id" in v and "message_id" in v:
                    STATE[k] = PollRef(chat_id=int(v["chat_id"]), message_id=int(v["message_id"]))
                else:
                    STATE[k] = None
    except Exception as e:
        logging.warning(f"Failed to load state: {e}")

def _save_state() -> None:
    try:
        out = {}
        for k, v in STATE.items():
            if isinstance(v, PollRef):
                out[k] = {"chat_id": v.chat_id, "message_id": v.message_id}
            else:
                out[k] = None
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f)
    except Exception as e:
        logging.warning(f"Failed to save state: {e}")

# ---------- Date helpers ----------
def next_weekday_date_exclusive(now_dt: datetime, weekday: int):
    days_ahead = (weekday - now_dt.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (now_dt + timedelta(days=days_ahead)).date()

def next_or_same_weekday_date(now_dt: datetime, weekday: int):
    days_ahead = (weekday - now_dt.weekday()) % 7
    return (now_dt + timedelta(days=days_ahead)).date()

def upcoming_friday_for_poll(now_dt: datetime):
    return next_weekday_date_exclusive(now_dt, 4)  # Friday

def upcoming_sunday_for_poll(now_dt: datetime):
    return next_weekday_date_exclusive(now_dt, 6)  # Sunday

def friday_for_reminder(now_dt: datetime):
    return next_or_same_weekday_date(now_dt, 4)

def sunday_for_reminder(now_dt: datetime):
    return next_or_same_weekday_date(now_dt, 6)

def ordinal(n: int) -> str:
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
    if update and update.effective_chat:
        return update.effective_chat.id
    return DEFAULT_CHAT_ID

async def _safe_pin(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    if not PIN_POLLS:
        return
    try:
        # Check pin permission
        member = await ctx.bot.get_chat_member(chat_id, (await ctx.bot.get_me()).id)
        can_pin = False
        if member.status == "creator":
            can_pin = True
        elif member.status == "administrator":
            can_pin = getattr(member, "can_pin_messages", False) or getattr(getattr(member, "privileges", None), "can_pin_messages", False)
        if not can_pin:
            await ctx.bot.send_message(chat_id, "⚠️ I need **Pin messages** permission to pin the poll.")
            return
        await ctx.bot.pin_chat_message(chat_id, message_id, disable_notification=True)
    except Exception as e:
        logging.warning(f"Pin failed: {e}")
        try:
            await ctx.bot.send_message(chat_id, f"⚠️ Couldn’t pin the poll ({e}). Please make me admin with **Pin messages**.")
        except Exception:
            pass

async def _send_cg_poll(ctx: ContextTypes.DEFAULT_TYPE, target_chat: int, target_date):
    msg = await ctx.bot.send_poll(
        chat_id=target_chat,
        question=f"Cell Group – {format_date_long(target_date)}",
        options=["🍽️ Dinner 7.15pm", "⛪ CG 8.15pm", "❌ Cannot make it"],
        is_anonymous=False,
        allows_multiple_answers=False,
    )
    STATE["cg_poll"] = PollRef(chat_id=target_chat, message_id=msg.message_id)
    _save_state()
    await _safe_pin(ctx, target_chat, msg.message_id)

async def _send_svc_poll(ctx: ContextTypes.DEFAULT_TYPE, target_chat: int, target_date):
    msg = await ctx.bot.send_poll(
        chat_id=target_chat,
        question=f"Sunday Service – {format_date_long(target_date)}",
        options=["⏰ 9am", "🕚 11.15am", "🙋 Serving", "🍽️ Lunch", "🧑‍🤝‍🧑 Invited a friend"],
        is_anonymous=False,
        allows_multiple_answers=True,
    )
    STATE["svc_poll"] = PollRef(chat_id=target_chat, message_id=msg.message_id)
    _save_state()
    await _safe_pin(ctx, target_chat, msg.message_id)

# ---------- Poll senders ----------
async def send_sunday_service_poll(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None, *, force: bool = False):
    now = datetime.now(SGT)
    if not force and now.weekday() != 4:  # Friday unless forced
        logging.warning(f"Service poll triggered {now:%a %Y-%m-%d %H:%M %Z}; skipping (expected Friday).")
        return
    target_chat = _effective_target_chat(update)
    target_date = upcoming_sunday_for_poll(now)
    await _send_svc_poll(ctx, target_chat, target_date)

async def send_cell_group_poll(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None, *, force: bool = False):
    now = datetime.now(SGT)
    if not force and now.weekday() != 6:  # Sunday unless forced
        logging.warning(f"CG poll triggered {now:%a %Y-%m-%d %H:%M %Z}; skipping (expected Sunday).")
        return
    target_chat = _effective_target_chat(update)
    target_date = upcoming_friday_for_poll(now)
    await _send_cg_poll(ctx, target_chat, target_date)

# ---------- Reminders ----------
async def remind_sunday_service(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None):
    now = datetime.now(SGT)
    date_txt = format_date_plain(sunday_for_reminder(now))
    ref = STATE.get("svc_poll")
    if isinstance(ref, PollRef):
        await ctx.bot.send_message(
            chat_id=ref.chat_id,
            text=f"⏰ Reminder: Please vote on the Sunday Service poll above for {date_txt}.",
            reply_to_message_id=ref.message_id,
            allow_sending_without_reply=True,
        )
    else:
        await ctx.bot.send_message(chat_id=DEFAULT_CHAT_ID, text=f"⏰ Reminder: Please vote on the Sunday Service poll for {date_txt}.")

async def remind_cell_group(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None):
    now = datetime.now(SGT)
    date_txt = format_date_plain(friday_for_reminder(now))
    ref = STATE.get("cg_poll")
    if isinstance(ref, PollRef):
        await ctx.bot.send_message(
            chat_id=ref.chat_id,
            text=f"⏰ Reminder: Please vote on the Cell Group poll above for {date_txt}.",
            reply_to_message_id=ref.message_id,
            allow_sending_without_reply=True,
        )
    else:
        await ctx.bot.send_message(chat_id=DEFAULT_CHAT_ID, text=f"⏰ Reminder: Please vote on the Cell Group poll for {date_txt}.")

# ✅ Force reminder (always posts; will reply to poll if we have it)
async def remind_cell_group_force(ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(SGT)
    date_txt = format_date_plain(friday_for_reminder(now))
    ref = STATE.get("cg_poll")
    if isinstance(ref, PollRef):
        await ctx.bot.send_message(
            chat_id=ref.chat_id,
            text=f"⏰ Reminder: Please vote on the Cell Group poll above for {date_txt}.",
            reply_to_message_id=ref.message_id,
            allow_sending_without_reply=True,
        )
    else:
        await ctx.bot.send_message(chat_id=DEFAULT_CHAT_ID, text=f"⏰ Reminder: Please vote on the Cell Group poll for {date_txt}.")

# Wrapper for job queue to force-post CG poll
async def post_cg_poll_force(ctx: ContextTypes.DEFAULT_TYPE):
    await send_cell_group_poll(ctx, update=None, force=True)

# ---------- Debug helpers ----------
def _next_occurrence(now: datetime, weekday: int, hh: int, mm: int) -> datetime:
    days_ahead = (weekday - now.weekday()) % 7
    candidate = datetime(now.year, now.month, now.day, hh, mm, tzinfo=SGT) + timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate

async def when_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(SGT)
    next_cg   = _next_occurrence(now, 6, 18, 0)   # Sun 18:00
    next_svc  = _next_occurrence(now, 4, 23, 30)  # Fri 23:30
    next_rm_m = _next_occurrence(now, 0, 18, 0)   # Mon 18:00
    next_rm_t = _next_occurrence(now, 3, 18, 0)   # Thu 18:00
    next_rm_f = _next_occurrence(now, 4, 15, 0)   # Fri 15:00
    next_rs   = _next_occurrence(now, 5, 12, 0)   # Sat 12:00
    await update.message.reply_text(
        "🗓️ Next (SGT):\n"
        f"• CG poll: {next_cg:%a %d %b %Y %H:%M}\n"
        f"• Service poll: {next_svc:%a %d %b %Y %H:%M}\n"
        f"• CG reminders: Mon {next_rm_m:%H:%M}, Thu {next_rm_t:%H:%M}, Fri {next_rm_f:%H:%M}\n"
        f"• Service reminder: {next_rs:%a %d %b %Y %H:%M}"
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
        "/cgpoll → post CG poll (in this chat)\n"
        "/cgrm → reminder for last CG poll\n"
        "/sunpoll → post Service poll (in this chat)\n"
        "/sunrm → reminder for last Service poll\n"
        "/when → show next scheduled times\n"
        "/testpoll → test poll\n"
        "/id → show chat id"
    )

async def cgpoll_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_cell_group_poll(ctx, update, force=True)  # force for manual

async def cgrm_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await remind_cell_group(ctx, update)

async def sunpoll_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_sunday_service_poll(ctx, update, force=True)  # force for manual

async def sunrm_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await remind_sunday_service(ctx, update)

async def testpoll_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target_chat = _effective_target_chat(update)
    await ctx.bot.send_poll(
        chat_id=target_chat,
        question="🚀 Test Poll – working?",
        options=["Yes 👍", "No 👎"],
        is_anonymous=False,
        allows_multiple_answers=False,
    )

async def id_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat
