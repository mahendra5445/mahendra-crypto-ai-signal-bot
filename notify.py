"""
Channel broadcast helper.

The original bot sent every message to a list of registered users
(admins.json, populated via /start). This clone instead posts every
auto-signal, trade-monitor update, watchdog alert, and daily summary to
a single Telegram channel (config.CHANNEL_ID) — the bot must be an
admin of that channel with "Post Messages" permission.
"""

import logging

from config import CHANNEL_ID

logger = logging.getLogger(__name__)


async def notify_channel(application, text: str) -> None:
    if not CHANNEL_ID:
        logger.warning("[CHANNEL] CHANNEL_ID not set — message not sent.")
        return
    try:
        await application.bot.send_message(chat_id=CHANNEL_ID, text=text)
    except Exception as e:
        logger.error(f"[CHANNEL SEND ERROR] {CHANNEL_ID}: {e}")
