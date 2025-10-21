import os, json, logging
from dataclasses import dataclass
from datetime import datetime, timedelta, time, timezone
from typing import Optional, Dict, Tuple
import time as _time

# ---- Telegram imports (and version log) ----
import telegram
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes, Defaults
from telegram.request import HTTPXRequest

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
PIN_POLLS = True

# Persist last poll IDs so reminders can reply even after a restart
STATE_PATH = "./state.json"

# ---- Timezone: robust SGT (fallback to fixed +08:00, never UTC) ----
try:
    from zoneinfo import ZoneInfo
    SGT = ZoneInfo("Asia/Singapore")
except Exception:
    SGT = timezone(timedelta(hours=8), name="SGT")  # reliable fallback

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.info(f"python-telegram-bot version: {getattr(telegram, '__version__', 'unknown')}")

# ---------- Poll tracking ----------
@dataclass
class PollRef:
    chat_id: int
    message_id: int

STATE: Dict[str, Optional[PollRef]] = {"cg_poll": None, "svc_poll": None}

def _load_state() -> None:
    global STATE
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for k in ("cg_poll", "svc_poll"):
                v = raw.get(k)
                STATE[k] = PollRef(int(v["chat_id"]), int(v["message_id"])) if v else None
    except Exception as e:
        logging.warning(f"Failed to load state: {e}")

def _save_state() -> None:
    try:
        out = {}
        for k, v in STATE.items():
            out[k] = {"chat_id": v.chat_id, "message_id": v.message_id} if isinstance(v, PollRef) else None
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f)
    except Exception as e:
        logging.warning(f"Failed to save state: {e}")

# ---------- Date helpers ----------
def next_weekday_date_exclusive(now_dt: datetime, weekday: int):
    days_ahead = (weekday - now_dt.weekday()) % 7
    if days_ahead == 0: days_ahead = 7
    return (now_dt + timedelta(days=days_ahead)).date()

def next_or_same_weekday_date(now_dt: datetime, weekday: int):
    days_ahead = (weekday - now_dt.weekday()) % 7
    return (now_dt + timedelta(days=days_ahead)).date()

def upcoming_friday_for_poll(now_dt: datetime):  # CG poll question date
    return next_weekday_date_exclusive(now_dt, 4)

def upcoming_sunday_for_poll(now_dt: datetime):  # Service poll question date
    return next_weekday_date_exclusive(now_dt, 6)

def friday_for_reminder(now_dt: datetime):
    return next_or_same_weekday_date(now_dt, 4)

def sunday_for_reminder(now_dt: datetime):
    return next_or_same_weekday_date(now_dt, 6)

def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20: suf = "th"
    else: suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"

def format_date_long(d) -> str:   # e.g., 31st August 2025 (Sun)
    return f"{ordinal(d.day)} {d.strftime('%B %Y')} ({d.strftime('%a')})"

def format_date_plain(d) -> str:  # e.g., 31st August 2025
    return f"{ordinal(d.day)} {d.strftime('%B %Y')}"

def _effective_target_chat(update: Optional[Update]) -> int:
    return update.effective_chat.id if (update and update.effective_chat) else DEFAULT_CHAT_ID

# ---------- Pin helper ----------
async def _safe_pin(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    if not PIN_POLLS: return
    try:
        me = await ctx.bot.get_me()
        member = await ctx.bot.get_chat_member(chat_id, me.id)
        can_pin = False
        if member.status == "creator": can_pin = True
        elif member.status == "administrator":
            can_pin = getattr(member, "can_pin_messages", False) or getattr(getattr(member, "privileges", None), "can_pin_messages", False)
        if not can_pin:
            await ctx.bot.send_message(chat_id, "⚠️ I need **Pin messages** permission to pin the poll.")
            return
        await ctx.bot.pin_chat_message(chat_id, message_id, disable_notification=True)
    except Exception as e:
        logging.warning(f"Pin failed: {e}")
        try:
            await ctx.bot.send_message(chat_id, f"⚠️ Couldn't pin the poll ({e}). Please grant **Pin messages**.")
        except Exception:
            pass

# ---------- Poll senders ----------
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

# Scheduler uses force=False; manual commands pass force=True
async def send_sunday_service_poll(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None, *, force: bool = False):
    now = datetime.now(SGT)
    if not force and now.weekday() != 4:  # Friday
        logging.warning(f"Service poll triggered {now:%a %Y-%m-%d %H:%M %Z}; skipping (expected Friday).")
        return
    await _send_svc_poll(ctx, _effective_target_chat(update), upcoming_sunday_for_poll(now))

async def send_cell_group_poll(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None, *, force: bool = False):
    now = datetime.now(SGT)
    if not force and now.weekday() != 6:  # Sunday
        logging.warning(f"CG poll triggered {now:%a %Y-%m-%d %H:%M %Z}; skipping (expected Sunday).")
        return
    await _send_cg_poll(ctx, _effective_target_chat(update), upcoming_friday_for_poll(now))

# ---------- Reminders (with weekday/time guards for scheduler) ----------
async def remind_sunday_service(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None):
    now = datetime.now(SGT)
    # If called by scheduler (update is None), enforce Sat 12:00 only
    if update is None:
        if not (now.weekday() == 5 and now.hour == 12):
            logging.info(f"Suppressed Service reminder off-window: {now.isoformat()}")
            return
    date_txt = format_date_plain(sunday_for_reminder(now))
    ref = STATE.get("svc_poll")
    if isinstance(ref, PollRef):
        await ctx.bot.send_message(ref.chat_id, f"⏰ Reminder: Please vote on the Sunday Service poll above for {date_txt}.",
                                   reply_to_message_id=ref.message_id, allow_sending_without_reply=True)
    else:
        await ctx.bot.send_message(DEFAULT_CHAT_ID, f"⏰ Reminder: Please vote on the Sunday Service poll for {date_txt}.")

async def remind_cell_group(ctx: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None):
    now = datetime.now(SGT)
    # If called by scheduler (update is None), allow only Mon 18:00, Thu 18:00, Fri 15:00
    if update is None:
        wk, hr = now.weekday(), now.hour
        if not ((wk == 0 and hr == 18) or (wk == 3 and hr == 18) or (wk == 4 and hr == 15)):
            logging.info(f"Suppressed CG reminder off-window: {now.isoformat()}")
            return
    date_txt = format_date_plain(friday_for_reminder(now))
    ref = STATE.get("cg_poll")
    if isinstance(ref, PollRef):
        await ctx.bot.send_message(ref.chat_id, f"⏰ Reminder: Please vote on the Cell Group poll above for {date_txt}.",
                                   reply_to_message_id=ref.message_id, allow_sending_without_reply=True)
    else:
        await ctx.bot.send_message(DEFAULT_CHAT_ID, f"⏰ Reminder: Please vote on the Cell Group poll for {date_txt}.")

# ==== Forced versions for one-off tests (ignore weekday/time guards) ====
async def post_cg_poll_force(ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(SGT)
    await _send_cg_poll(ctx, DEFAULT_CHAT_ID, upcoming_friday_for_poll(now))

async def remind_cell_group_force(ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(SGT)
    ref = STATE.get("cg_poll")
    date_txt = format_date_plain(friday_for_reminder(now))
    if isinstance(ref, PollRef):
        await ctx.bot.send_message(ref.chat_id, f"⏰ Reminder: Please vote on the Cell Group poll above for {date_txt}.",
                                   reply_to_message_id=ref.message_id, allow_sending_without_reply=True)
    else:
        await ctx.bot.send_message(DEFAULT_CHAT_ID, f"⏰ Reminder: Please vote on the Cell Group poll for {date_txt}.")

# ---------- /when & /jobs ----------
def _next_occurrence(now: datetime, weekday: int, hh: int, mm: int) -> datetime:
    days_ahead = (weekday - now.weekday()) % 7
    candidate = datetime(now.year, now.month, now.day, hh, mm, tzinfo=SGT) + timedelta(days=days_ahead)
    if candidate <= now: candidate += timedelta(days=7)
    return candidate

async def when_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(SGT)
    next_cg   = _next_occurrence(now, 6, 18, 0)
    next_svc  = _next_occurrence(now, 4, 23, 30)
    next_rm_m = _next_occurrence(now, 0, 18, 0)
    next_rm_t = _next_occurrence(now, 3, 18, 0)
    next_rm_f = _next_occurrence(now, 4, 15, 0)
    next_rs   = _next_occurrence(now, 5, 12, 0)
    await update.message.reply_text(
        "🗓️ Next (SGT):\n"
        f"• CG poll: {next_cg:%a %d %b %Y %H:%M}\n"
        f"• Service poll: {next_svc:%a %d %b %Y %H:%M}\n"
        f"• CG reminders: Mon {next_rm_m:%H:%M}, Thu {next_rm_t:%H:%M}, Fri {next_rm_f:%H:%M}\n"
        f"• Service reminder: {next_rs:%a %d %b %Y %H:%M}"
    )

async def jobs_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    jobs = ctx.job_queue.jobs()
    if not jobs:
        await update.message.reply_text("No scheduled jobs.")
        return
    now = datetime.now(SGT)
    lines = []
    for j in jobs:
        if j.next_t:
            next_local = j.next_t.astimezone(SGT)
            delta = int((next_local - now).total_seconds())
            lines.append(f"• {j.name} → {next_local:%a %d %b %Y %H:%M:%S} (in {delta}s)")
        else:
            lines.append(f"• {j.name} → n/a")
    await update.message.reply_text("🧰 Pending jobs:\n" + "\n".join(lines))

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
        "/jobs → list scheduled jobs\n"
        "/testpoll → test poll\n"
        "/id → show chat id"
    )

async def cgpoll_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_cell_group_poll(ctx, update, force=True)

async def cgrm_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await remind_cell_group(ctx, update)

async def sunpoll_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_sunday_service_poll(ctx, update, force=True)

async def sunrm_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await remind_sunday_service(ctx, update)

async def testpoll_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target_chat = _effective_target_chat(update)
    await ctx.bot.send_poll(chat_id=target_chat, question="🚀 Test Poll – working?",
                            options=["Yes 👍", "No 👎"], is_anonymous=False, allows_multiple_answers=False)

async def id_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.message.reply_text(f"Chat type: {chat.type}\nChat ID: {chat.id}")

# ---------- Weekly Scheduler ----------
def schedule_jobs(app: Application):
    jq = app.job_queue
    jq.run_daily(send_cell_group_poll, time=time(18, 0, tzinfo=SGT), days=(Days.SUNDAY,))
    jq.run_daily(remind_cell_group,    time=time(18, 0, tzinfo=SGT), days=(Days.MONDAY,))
    jq.run_daily(remind_cell_group,    time=time(18, 0, tzinfo=SGT), days=(Days.THURSDAY,))
    jq.run_daily(remind_cell_group,    time=time(15, 0, tzinfo=SGT), days=(Days.FRIDAY,))
    jq.run_daily(send_sunday_service_poll, time=time(23, 30, tzinfo=SGT), days=(Days.FRIDAY,))
    jq.run_daily(remind_sunday_service,    time=time(12,  0, tzinfo=SGT), days=(Days.SATURDAY,))

# ---------- Catch-up on start ----------
def catchup_on_start(app: Application):
    _load_state()
    now = datetime.now(SGT)
    jq = app.job_queue

    if STATE.get("cg_poll") is None:
        days_to_sun = (6 - now.weekday()) % 7
        sun_target = datetime(now.year, now.month, now.day, 18, 0, tzinfo=SGT) + timedelta(days=days_to_sun)
        if now > sun_target:
            jq.run_once(send_cell_group_poll, when=2, name="CATCHUP_CG_POLL")

    if STATE.get("svc_poll") is None:
        days_to_fri = (4 - now.weekday()) % 7
        fri_target = datetime(now.year, now.month, now.day, 23, 30, tzinfo=SGT) + timedelta(days=days_to_fri)
        if now > fri_target:
            jq.run_once(send_sunday_service_poll, when=2, name="CATCHUP_SVC_POLL")

    def _maybe_catchup(weekday: int, hh: int, mm: int, job, name: str):
        if now.weekday() == weekday and now.time() >= time(hh, mm):
            jq.run_once(job, when=3, name=name)

    _maybe_catchup(Days.MONDAY,    18, 0, remind_cell_group,     "CATCHUP_CG_MON_1800")
    _maybe_catchup(Days.THURSDAY,  18, 0, remind_cell_group,     "CATCHUP_CG_THU_1800")
    _maybe_catchup(Days.FRIDAY,    15, 0, remind_cell_group,     "CATCHUP_CG_FRI_1500")
    _maybe_catchup(Days.SATURDAY,  12, 0, remind_sunday_service, "CATCHUP_SVC_SAT_1200")

# ---------- One-off arm for TODAY (3:45 poll, 3:50 reminder) ----------
def arm_today_1545_1550(app: Application):
    """
    Arms:
      - CG poll TODAY at 15:45 SGT (forced)
      - CG reminder TODAY at 15:50 SGT (forced)
    If already past, falls back to poll ~60s from now and reminder ~5 minutes after poll.
    Also posts an immediate 'armed' message in the group.
    """
    jq = app.job_queue
    now = datetime.now(SGT)

    poll_dt = datetime(now.year, now.month, now.day, 15, 45, 0, tzinfo=SGT)
    rm_dt   = datetime(now.year, now.month, now.day, 15, 50, 0, tzinfo=SGT)

    if poll_dt <= now:
        poll_dt = now + timedelta(seconds=60)
    if rm_dt <= now:
        rm_dt = poll_dt + timedelta(minutes=5)

    jq.run_once(post_cg_poll_force,      when=poll_dt, name="ONEOFF_CG_POLL_TODAY_1545")
    jq.run_once(remind_cell_group_force, when=rm_dt,   name="ONEOFF_CG_REM_TODAY_1550")

    async def announce(ctx: ContextTypes.DEFAULT_TYPE):
        try:
            await ctx.bot.send_message(
                chat_id=DEFAULT_CHAT_ID,
                text=f"🔔 Armed one-off test:\n• CG poll at {poll_dt.astimezone(SGT):%a %d %b %Y %H:%M} SGT"
                     f"\n• CG reminder at {rm_dt.astimezone(SGT):%a %d %b %Y %H:%M} SGT"
            )
        except Exception as e:
            logging.warning(f"Announce failed: {e}")
    jq.run_once(announce, when=1, name="ONEOFF_CG_ANNOUNCE")

# ---------- Build & Run (HTTP timeouts + SGT Defaults) ----------
def build_app() -> Application:
    request = HTTPXRequest(connect_timeout=20, read_timeout=30, write_timeout=30, pool_timeout=30)
    defaults = Defaults(tzinfo=SGT)
    app = Application.builder().token(TOKEN).request(request).defaults(defaults).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cgpoll", cgpoll_cmd))
    app.add_handler(CommandHandler("cgrm", cgrm_cmd))
    app.add_handler(CommandHandler("sunpoll", sunpoll_cmd))
    app.add_handler(CommandHandler("sunrm", sunrm_cmd))
    app.add_handler(CommandHandler("when", when_cmd))
    app.add_handler(CommandHandler("jobs", jobs_cmd))
    app.add_handler(CommandHandler("testpoll", testpoll_cmd))
    app.add_handler(CommandHandler("id", id_cmd))
    app.add_error_handler(error_handler)

    # Jobs
    schedule_jobs(app)
    catchup_on_start(app)

    # Startup confirmations
    app.job_queue.run_once(_startup_ping, when=1, name="STARTUP_PING")
    app.job_queue.run_once(_register_commands, when=2, name="REGISTER_COMMANDS")

    # Arm one-off test (today 15:45 / 15:50 SGT)
    arm_today_1545_1550(app)

    return app

async def _startup_ping(ctx: ContextTypes.DEFAULT_TYPE):
    try:
        me = await ctx.bot.get_me()
        await ctx.bot.send_message(DEFAULT_CHAT_ID, f"✅ Online as @{me.username} ({me.id}). Target chat: {DEFAULT_CHAT_ID}")
    except Exception as e:
        logging.warning(f"Startup ping failed: {e}")

async def _register_commands(ctx: ContextTypes.DEFAULT_TYPE):
    try:
        cmds = [
            BotCommand("start", "Show schedule and commands"),
            BotCommand("cgpoll", "Post Cell Group poll"),
            BotCommand("cgrm", "Reminder for last CG poll"),
            BotCommand("sunpoll", "Post Sunday Service poll"),
            BotCommand("sunrm", "Reminder for last Sunday Service poll"),
            BotCommand("when", "Show next scheduled times"),
            BotCommand("jobs", "List scheduled jobs"),
            BotCommand("testpoll", "Post a test Yes/No poll"),
            BotCommand("id", "Show chat id"),
        ]
        await ctx.bot.set_my_commands(cmds)
    except Exception as e:
        logging.warning(f"set_my_commands failed: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled exception while handling update: %s", update, exc_info=context.error)

def main():
    backoff = 10
    while True:
        try:
            app = build_app()
            logging.info("Bot starting…")
            app.run_polling(drop_pending_updates=True)
            break
        except telegram.error.TimedOut as e:
            logging.warning(f"Telegram API timed out at startup; retrying in {backoff}s: {e}")
            _time.sleep(backoff)
        except Exception as e:
            logging.exception(f"Fatal error; retrying in {backoff}s")
            _time.sleep(backoff)

if __name__ == "__main__":
    main()
