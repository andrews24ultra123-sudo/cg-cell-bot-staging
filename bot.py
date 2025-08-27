import os
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# --- Staging Hardcoded Config ---
STAGE = "staging"

# Telegram bot token and chat id (staging)
BOT_TOKEN = "8179378309:AAGbscsJJ0ScMKEna_j-2kVfrcx0TL8Mn80"
CHAT_ID   = 54380770   # personal chat id

# Timezone
TZ = "Asia/Seoul"

# Telegram send helper
def telegram_send(chat_id: int, text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    resp = requests.post(url, json=payload)
    return resp.json()

# Wrapper to prefix staging messages
def send_message_safe(text: str):
    prefix = "[STAGING] "
    return telegram_send(CHAT_ID, prefix + text)

# Reminder job
def remind_poll():
    send_message_safe("⏰ Cell group poll reminder — please vote!")

# Scheduler setup
tz = pytz.timezone(TZ)
sched = BackgroundScheduler(timezone=tz)

# Reminders
sched.add_job(remind_poll, CronTrigger(day_of_week="mon", hour=18, minute=0))  # Monday 6:00pm
sched.add_job(remind_poll, CronTrigger(day_of_week="thu", hour=18, minute=0))  # Thursday 6:00pm
sched.add_job(remind_poll, CronTrigger(day_of_week="fri", hour=15, minute=0))  # Friday 3:00pm

# (Optional) Fast test job in staging — fires every 2 minutes
# Comment this out if you don’t need rapid testing
sched.add_job(remind_poll, CronTrigger(minute="*/2"))

sched.start()

# --- Example: trigger once on startup ---
if __name__ == "__main__":
    send_message_safe("🚀 CG Cell Bot (Staging) started successfully.")
