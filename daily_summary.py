"""
Daily Summary Job

Sends one digest message per day covering every asset's signal count
and win rate for that day (based on trade_tracker.get_stats(since=...)),
so the user doesn't have to manually run /stats to keep track.

Scheduling note: runs at SUMMARY_HOUR:SUMMARY_MINUTE in the SERVER's
local time (same clock trade_tracker uses for trade["time"], so the
"today" filter lines up correctly). On Railway this is typically UTC —
adjust SUMMARY_HOUR below if you want it at a specific IST time instead
(e.g. IST 23:30 = UTC 18:00 in winter / UTC 18:00 year-round since IST
has no DST → SUMMARY_HOUR = 18 for a ~11:00 PM IST digest).
"""

import asyncio
import logging
from datetime import datetime, timedelta

from config import ASSETS
from notify import notify_channel
from trade_tracker import get_stats

logger = logging.getLogger(__name__)

SUMMARY_HOUR = 18     # server-local hour to send the digest (see note above)
SUMMARY_MINUTE = 0


async def _notify_all(application, text: str) -> None:
    await notify_channel(application, text)


def _seconds_until_next_run() -> float:
    now = datetime.now()
    target = now.replace(hour=SUMMARY_HOUR, minute=SUMMARY_MINUTE, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _build_summary_text() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    overall = get_stats(since=today)

    lines = [f"📅 DAILY SUMMARY — {today}\n"]

    if overall["total"] == 0:
        lines.append("No signals today.")
        return "\n".join(lines)

    lines.append(
        f"📈 Total Signals : {overall['total']}\n"
        f"🎯 TP Hit        : {overall['tp']}\n"
        f"⚪ Breakeven     : {overall['be']}\n"
        f"🛑 SL Hit        : {overall['sl']}\n"
        f"🏆 Win Rate      : {overall['win_rate']}%\n"
    )

    lines.append("Per-Asset:")
    any_asset_line = False
    for asset, cfg in ASSETS.items():
        s = get_stats(asset=asset, since=today)
        if s["total"] == 0:
            continue
        any_asset_line = True
        lines.append(
            f"  {cfg['label']:<10} {s['total']:>2} signals | "
            f"TP {s['tp']} / SL {s['sl']} / BE {s['be']} | "
            f"{s['win_rate']}% win"
        )
    if not any_asset_line:
        lines.append("  (none)")

    return "\n".join(lines)


async def daily_summary_job(application) -> None:
    logger.info(
        f"[DAILY SUMMARY] Started — will send daily at "
        f"{SUMMARY_HOUR:02d}:{SUMMARY_MINUTE:02d} server-local time"
    )
    while True:
        await asyncio.sleep(_seconds_until_next_run())
        try:
            text = _build_summary_text()
            await _notify_all(application, text)
            logger.info("[DAILY SUMMARY] Sent")
        except Exception as e:
            logger.error(f"[DAILY SUMMARY] Failed: {e}")
        # Sleep a bit past the minute so we don't immediately re-trigger
        # the same slot if this iteration ran slightly early/late.
        await asyncio.sleep(70)
