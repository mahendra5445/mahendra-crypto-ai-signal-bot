"""
Watchdog Job

Purpose: catch SILENT failures — cases where auto_signal_job's loop is
technically still alive but hasn't completed a real cycle in a long time
(e.g. Yahoo Finance has been failing for every asset for hours, or an
unexpected exception outside the per-asset try/except is looping fast
without ever reaching the heartbeat stamp).

Without this, the bot could sit quietly broken for hours and the only
sign would be "no signals" — which looks identical to "market is just
quiet". This job makes that distinction visible.

How it works:
  - auto_signal.py stamps shared_state.heartbeat["last_cycle"] = time.time()
    at the end of every full asset-check cycle (normally every ~15 min,
    or ~5 min when paused for high-impact news).
  - This job wakes up every CHECK_INTERVAL and compares "now" against
    that stamp. If it's older than STALE_THRESHOLD, something is stuck.
  - Sends ONE alert (not one every check) until the loop recovers, so a
    genuinely stuck bot doesn't spam Telegram every few minutes.
"""

import asyncio
import logging
import time

from notify import notify_channel
from shared_state import heartbeat

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 300          # check every 5 minutes
STALE_THRESHOLD = 40 * 60     # alert if no cycle completed in 40 min
                               # (normal cycle is 15 min, or 5 min during
                               # a news pause — 40 min gives generous
                               # margin for a couple of retried API calls
                               # before flagging a real problem)

_alerted = False   # tracks whether we've already sent the "stuck" alert


async def _notify_all(application, text: str) -> None:
    await notify_channel(application, text)


async def watchdog_job(application) -> None:
    global _alerted
    logger.info("[WATCHDOG] Started")

    while True:
        await asyncio.sleep(CHECK_INTERVAL)

        last_cycle = heartbeat.get("last_cycle", 0.0)
        stale_for = time.time() - last_cycle

        if stale_for > STALE_THRESHOLD:
            if not _alerted:
                minutes = int(stale_for // 60)
                logger.error(
                    f"[WATCHDOG] Auto-signal loop stuck — no cycle in {minutes} min"
                )
                await _notify_all(
                    application,
                    "⚠️ WATCHDOG ALERT\n\n"
                    f"Auto-signal loop hasn't completed a cycle in "
                    f"{minutes} minutes (expected every ~15 min).\n\n"
                    "Possible causes: Yahoo Finance repeatedly failing, "
                    "network issue, or an unhandled crash in the signal "
                    "loop. Check Railway logs.\n\n"
                    "This alert won't repeat until the loop recovers."
                )
                _alerted = True
        else:
            if _alerted:
                logger.info("[WATCHDOG] Auto-signal loop recovered")
                await _notify_all(
                    application,
                    "✅ WATCHDOG — Auto-signal loop is back to normal."
                )
            _alerted = False
