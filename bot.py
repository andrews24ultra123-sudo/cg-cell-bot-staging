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
PIN_POLLS = True  # pin each new poll

SGT = ZoneInfo("Asia/Singapore")
logging.basicConfig(level=logging.INFO, form
