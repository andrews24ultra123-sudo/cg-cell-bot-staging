import logging
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ======== HARD-CODED TOKEN & CHAT ID ========
TOKEN = "8179378309:AAGbscsJJ0ScMKEna_j-2kVfrcx0TL8Mn80"
CHAT_ID = 54380770  # personal chat ID

# Pin polls? (True/False)
PIN_POLLS = False

SGT = ZoneInfo("Asia/Singapore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# In-memory storage (resets if container restarts)
STATE = {"cg_poll_msg_id": None, "svc_poll_msg_id": None}

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

# ---------- Poll senders ----------
async def send_sunday_service_poll(ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(SGT)
    target = upcoming_sunday(now)
    msg = await ctx.bot.send_poll(
        chat_id=CHAT_ID,
        question=f"Sunday Service – {target:%Y-%m-%d (%a)}",
        options=["9am", "11.15am", "Serving", "Lunch", "Invited a friend"],
        is_anonymous=False,
        allows_multiple_answers=True,
    )
    STATE["svc_poll_msg_id"] = msg.message_id
    if PIN_POLLS:
        try:
            await ctx.bot.pin_chat_message(CHAT_ID, msg.message_id, disable_notification=True)
        except Exception as e:
            logging.warning(f"Pin failed: {e}")

async def send_cell_group_poll(ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(SGT)
    target = upcoming_friday(now)
    msg = await ctx.bot.send_poll(
        chat_id=CHAT_ID,
        question=f"Cell Group – {target:%Y-%m-%d (%a)}",
        options=["Dinner 7.15pm", "CG 8.15pm", "Cannot make it"],
        is_anonymous=False,
        allows_multiple_answers=False,
    )
    STATE["cg_poll_msg_id"] = msg.message_id
    if PIN_POLLS:
        try:
            await ctx.bot.pin_chat_message(CHAT_ID, msg.message_id, disable_notification=True)
        except Exception as e:
            logging.warning(f"Pin failed: {e}")

# ---------- Reminders (fixed: no ReplyParameters) ----------
async def remind_sunday_service(ctx: ContextTypes.DEFAULT_TYPE):
    poll_id = STATE.get("svc_poll_msg_id")
    try:
        if poll_id:
            await ctx.bot.send_message(
                chat_id=CHAT_ID,
                text="⏰ Reminder: Please vote on the Sunday Service poll above 🙏",
                reply_to_message_id=poll_id,
                allow_sending_without_reply=True,
            )
        else:
            await ctx.bot.send_message(
                chat_id=CHAT_ID,
                text="⏰ Reminder: Please vote on the Sunday Service poll."
            )
    except Exception as e:
        logging.exception(f"/sunrm failed: {e}")

async def remind_cell_group(ctx: ContextTypes.DEFAULT_TYPE):
    poll_id = STATE.get("cg_poll_msg_id")
    try:
        if poll_id:
            await ctx.bot.send_message(
                chat_id=CHAT_ID,
                text="⏰ Reminder: Please vote on the Cell Group poll above 👆",
                reply_to_message_id=poll_id,
                allow_sending_without_reply=True,
            )
        else:
            await ctx.bot.send_message(
                chat_id=CHAT_ID,
                text="⏰ Reminder: Please vote on the Cell Group poll."
            )
    except Exception as e:
        logging.exception(f"/cgrm failed: {e}")

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
        "/cgpoll → post CG poll\n"
        "/cgrm → reminder for last CG poll\n"
        "/sunpoll → post Service poll\n"
        "/sunrm → reminder for last Service poll\n"
        "/testpoll → test poll\n"
        "/id → show chat id"
    )

async def cgpoll_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_cell_group_poll(ctx)

async def cgrm_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await remind_cell_group(ctx)

async def sunpoll_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_sunday_service_poll(ctx)

async def sunrm_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await remind_sunday_service(ctx)

async def testpoll_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ctx.bot.send_poll(
        chat_id=CHAT_ID,
        question="🚀 Test Poll – working?",
        options=["Yes 👍", "No 👎"],
        is_anonymous=False,
        allows_multiple_answers=False,
    )

async def id_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.message.reply_text(f"Chat type: {chat.type}\nChat ID: {chat.id}")

# ---------- Scheduler ----------
def schedule_jobs(app: Application):
    jq = app.job_queue
    # CG: poll Sun, reminders Mon + Thu + Fri(3pm)
    jq.run_daily(send_cell_group_poll, time=time(18, 0, tzinfo=SGT), days=(6,))  # Sunday 6pm → POST POLL
    jq.run_daily(remind_cell_group,    time=time(18, 0, tzinfo=SGT), days=(0,))  # Monday 6pm → REMINDER
    jq.run_daily(remind_cell_group,    time=time(18, 0, tzinfo=SGT), days=(3,))  # Thursday 6pm → REMINDER
    jq.run_daily(remind_cell_group,    time=time(15, 0, tzinfo=SGT), days=(4,))  # Friday 3pm → REMINDER
    # Service: poll Fri, reminder Sat
    jq.run_daily(send_sunday_service_poll, time=time(23, 30, tzinfo=SGT), days=(4,))  # Friday 11:30pm → POST POLL
    jq.run_daily(remind_sunday_service,    time=time(12, 0,  tzinfo=SGT), days=(5,))  # Saturday 12pm → REMINDER

# ---------- Main ----------
def main():
    app = Application.builder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cgpoll", cgpoll_cmd))
    app.add_handler(CommandHandler("cgrm", cgrm_cmd))
    app.add_handler(CommandHandler("sunpoll", sunpoll_cmd))
    app.add_handler(CommandHandler("sunrm", sunrm_cmd))
    app.add_handler(CommandHandler("testpoll", testpoll_cmd))
    app.add_handler(CommandHandler("id", id_cmd))

    # Jobs
    schedule_jobs(app)

    logging.info("Bot starting…")
    app.run_polling(allowed_updates=None)

if __name__ == "__main__":
    main()
