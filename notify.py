"""
Channel broadcast helper.

The original bot sent every message to a list of registered users
(admins.json, populated via /start). This clone instead posts every
auto-signal, trade-monitor update, watchdog alert, and daily summary to
a single Telegram channel (config.CHANNEL_ID) — the bot must be an
admin of that channel with "Post Messages" permission.
"""

import asyncio
import logging

from config import CHANNEL_ID

logger = logging.getLogger(__name__)


async def notify_channel(application, text: str, retries: int = 3) -> bool:
    """
    Send a channel message with retries. Returns True on success.
    Callers that open trades MUST check the return value (C-003 / H-013).
    """
    if not CHANNEL_ID:
        logger.warning("[CHANNEL] CHANNEL_ID not set — message not sent.")
        return False
    delay = 1.0
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            await application.bot.send_message(chat_id=CHANNEL_ID, text=text)
            return True
        except Exception as e:
            last_err = e
            logger.error(
                f"[CHANNEL SEND ERROR] attempt {attempt + 1}/{retries} "
                f"{CHANNEL_ID}: {e}"
            )
            if attempt < retries - 1:
                await asyncio.sleep(delay)
                delay *= 2
    logger.error(f"[CHANNEL SEND ERROR] exhausted retries: {last_err}")
    return False
