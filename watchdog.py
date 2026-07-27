"""
Watchdog Job

Purpose: catch SILENT failures — cases where auto_signal_job or
trade_monitor_job is technically still alive but hasn't completed a real
cycle in a long time.
"""

import asyncio
import logging
import time

from config import MONITOR_INTERVAL_SEC
from notify import notify_channel
from shared_state import heartbeat

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 300
STALE_THRESHOLD = 40 * 60
MONITOR_STALE = max(3 * MONITOR_INTERVAL_SEC, 180)

_alerted_signal = False
_alerted_monitor = False


async def watchdog_job(application) -> None:
    global _alerted_signal, _alerted_monitor
    logger.info("[WATCHDOG] Started")

    while True:
        await asyncio.sleep(CHECK_INTERVAL)

        now = time.time()
        last_cycle = heartbeat.get("last_cycle", 0.0)
        last_monitor = heartbeat.get("last_monitor", 0.0)
        stale_signal = now - last_cycle
        stale_monitor = now - last_monitor if last_monitor else stale_signal

        if stale_signal > STALE_THRESHOLD:
            if not _alerted_signal:
                minutes = int(stale_signal // 60)
                logger.error(
                    f"[WATCHDOG] Auto-signal loop stuck — no cycle in {minutes} min"
                )
                await notify_channel(
                    application,
                    "⚠️ WATCHDOG ALERT\n\n"
                    f"Auto-signal loop hasn't completed a cycle in "
                    f"{minutes} minutes (expected every ~15 min).\n\n"
                    "Possible causes: Yahoo Finance repeatedly failing, "
                    "network issue, or an unhandled crash in the signal "
                    "loop. Check Railway logs.\n\n"
                    "This alert won't repeat until the loop recovers.",
                )
                _alerted_signal = True
        else:
            if _alerted_signal:
                logger.info("[WATCHDOG] Auto-signal loop recovered")
                await notify_channel(
                    application,
                    "✅ WATCHDOG — Auto-signal loop is back to normal.",
                )
            _alerted_signal = False

        # L-008: also watch the trade monitor
        if last_monitor and stale_monitor > MONITOR_STALE:
            if not _alerted_monitor:
                minutes = int(stale_monitor // 60)
                logger.error(
                    f"[WATCHDOG] Trade-monitor stuck — no poll in {minutes} min"
                )
                await notify_channel(
                    application,
                    "⚠️ WATCHDOG ALERT\n\n"
                    f"Trade monitor hasn't completed a poll in {minutes} minutes "
                    f"(expected every ~{MONITOR_INTERVAL_SEC}s).\n\n"
                    "Open trades may not be tracked until it recovers.",
                )
                _alerted_monitor = True
        else:
            if _alerted_monitor and last_monitor:
                logger.info("[WATCHDOG] Trade-monitor recovered")
                await notify_channel(
                    application,
                    "✅ WATCHDOG — Trade monitor is back to normal.",
                )
            _alerted_monitor = False
