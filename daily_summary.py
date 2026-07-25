"""
Daily Summary Job

Sends one digest message per day covering every asset's signal count
and win rate for that day (based on trade_tracker.get_stats(since=...)),
so the user doesn't have to manually run /stats to keep track.

Scheduling note: runs at SUMMARY_HOUR:SUMMARY_MINUTE on the bot's shared
clock (clock.py), the SAME clock trade_tracker stamps trades with and
guards counts the day with, so the "today" filter always lines up. That
clock defaults to IST (BOT_UTC_OFFSET_MIN=330); set that env var to 0 for
UTC or any other offset. So SUMMARY_HOUR = 21 means 21:00 in whatever
timezone BOT_UTC_OFFSET_MIN selects — 9:00 PM IST by default.
"""

import asyncio
import logging

from analytics import performance
from clock import now_local, today_str
from config import ASSETS
from notify import notify_channel
from trade_tracker import get_stats

logger = logging.getLogger(__name__)

SUMMARY_HOUR = 21     # hour on the shared clock (default 21:00 IST) to send
SUMMARY_MINUTE = 0


async def _notify_all(application, text: str) -> None:
    await notify_channel(application, text)


def _seconds_until_next_run() -> float:
    from datetime import timedelta
    now = now_local()
    target = now.replace(hour=SUMMARY_HOUR, minute=SUMMARY_MINUTE, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _build_summary_text() -> str:
    today = today_str()
    overall = get_stats(since=today)

    lines = [f"📅 DAILY SUMMARY — {today}\n"]

    if overall["total"] == 0:
        lines.append("No signals today.")
        return "\n".join(lines)

    perf = performance(since=today)
    lines.append(
        f"📈 Total Signals : {overall['total']}\n"
        f"🔵 Still Open    : {overall['open']}\n"
        f"🎯 TP Hit        : {overall['tp']}\n"
        f"⚪ Breakeven     : {overall['be']}\n"
        f"🛑 SL Hit        : {overall['sl']}\n"
        f"🏆 Win Rate      : {overall['win_rate']}%\n"
    )
    if perf.get("count"):
        lines.append(
            f"💹 Net           : {perf['total_r']:+.2f} R "
            f"({perf['expectancy_r']:+.3f} R/trade)\n"
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
        f"{SUMMARY_HOUR:02d}:{SUMMARY_MINUTE:02d} bot-clock time"
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
