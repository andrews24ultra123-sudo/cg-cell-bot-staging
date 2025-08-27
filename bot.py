import os, json, requests, pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ====== CONFIG (edit IDs if needed) ======
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")

# Your personal chat (for tests)
PERSONAL_CHAT_ID = 54380770

# Your separate cell group chat (actual reminders)
CELL_GROUP_CHAT_ID = -4680966417   # <- replace if your group is different

TZ = "Asia/Seoul"
STATE_FILE = "./active_chat.json"   # remembers which target is active

# ====== STATE HELPERS ======
def _load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"active_target": "cell"}  # "cell" or "personal" or "both"

def _save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)

def get_active_target():
    return _load_state().get("active_target", "cell")

def set_active_target(target: str):
    assert target in ("cell", "personal", "both")
    st = _load_state()
    st["active_target"] = target
    _save_state(st)
    return target

# ====== TELEGRAM SEND ======
def telegram_send(chat_id: int, text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    r = requests.post(url, json=payload, timeout=15)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "status_code": r.status_code, "text": r.text}

def send_to_target(text: str, target: str = None):
    """
    target: "cell" | "personal" | "both" | None(uses saved state)
    """
    if target is None:
        target = get_active_target()

    if target == "cell":
        return [telegram_send(CELL_GROUP_CHAT_ID, text)]
    elif target == "personal":
        return [telegram_send(PERSONAL_CHAT_ID, text)]
    elif target == "both":
        return [
            telegram_send(CELL_GROUP_CHAT_ID, text),
            telegram_send(PERSONAL_CHAT_ID, text),
        ]

# ====== COMMAND HELPERS (OPTIONAL) ======
# If you already parse Telegram commands elsewhere, just call these.
def cmd_use_cell():
    set_active_target("cell")
    send_to_target("✅ Active target set to *CELL GROUP*", target="personal")

def cmd_use_personal():
    set_active_target("personal")
    send_to_target("✅ Active target set to *PERSONAL* (your DM)", target="personal")

def cmd_use_both():
    set_active_target("both")
    send_to_target("✅ Active target set to *BOTH* (cell + personal)", target="personal")

def cmd_whereami():
    cur = get_active_target()
    send_to_target(f"ℹ️ Active target is **{cur.upper()}**", target="personal")

# ====== REMINDER JOBS (use your existing schedule) ======
def remind_poll():
    # alarm emoji per your preference
    send_to_target("⏰ Cell group poll reminder — please vote!")  # uses active target

# Want all reminders to always go to the cell group regardless of active target?
# -> change to: send_to_target("⏰ ...", target="cell")

# ====== SCHEDULER ======
tz = pytz.timezone(TZ)
sched = BackgroundScheduler(timezone=tz)

# Your existing times (Asia/Seoul)
sched.add_job(remind_poll, CronTrigger(day_of_week="mon", hour=18, minute=0))  # Mon 6:00pm
sched.add_job(remind_poll, CronTrigger(day_of_week="thu", hour=18, minute=0))  # Thu 6:00pm
sched.add_job(remind_poll, CronTrigger(day_of_week="fri", hour=15, minute=0))  # Fri 3:00pm

# For quick testing, uncomment this (remember to remove later):
# sched.add_job(remind_poll, CronTrigger(minute="*/2"))

sched.start()

# ====== BOOT MESSAGE ======
if __name__ == "__main__":
    # default target on first run
    if not os.path.exists(STATE_FILE):
        set_active_target("cell")  # change to "personal" if you prefer
    send_to_target("🚀 Poll automation online. Reminders armed.")
