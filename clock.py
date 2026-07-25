"""
One clock for the whole bot.

Every place that asks "what day is it" — the daily trade cap in guards.py,
the daily-summary schedule, and the timestamp stamped on each trade — used
to call datetime.now() independently, which is the SERVER's local time. On
Railway that is UTC, so the trading day rolled over at 00:00 UTC (05:30 IST)
and the digest fired on a UTC clock, which is confusing for a bot run from
India.

This module gives all of them a SINGLE wall clock with a configurable UTC
offset, so "today" means the same thing everywhere.

    BOT_UTC_OFFSET_MIN   minutes to add to UTC. Default 330 = IST.
                         Set 0 for plain UTC, -300 for US Eastern, etc.

Because trade["time"], the daily cap, the since-filter and the digest all
go through here, they can never disagree about where the day boundary is.
"""

import os
from datetime import datetime, timedelta, timezone


def _offset_min() -> int:
    try:
        return int(os.getenv("BOT_UTC_OFFSET_MIN", "330"))
    except (TypeError, ValueError):
        return 330


UTC_OFFSET_MIN = _offset_min()
_TZ = timezone(timedelta(minutes=UTC_OFFSET_MIN))


def now_local() -> datetime:
    """Timezone-aware 'wall clock' now, at the configured offset."""
    return datetime.now(_TZ)


def today_str() -> str:
    """YYYY-MM-DD for the current local day — the day-boundary key."""
    return now_local().strftime("%Y-%m-%d")


def fmt_ts(unix_ts: float) -> str:
    """Format a unix timestamp as 'YYYY-MM-DD HH:MM:SS' in local time."""
    return datetime.fromtimestamp(unix_ts, _TZ).strftime("%Y-%m-%d %H:%M:%S")
