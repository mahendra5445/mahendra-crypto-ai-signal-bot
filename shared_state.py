"""
Shared asyncio primitives used across multiple modules.

Keeping the lock here avoids circular imports between
auto_signal.py and trade_monitor.py.
"""

import asyncio

# Single lock that serialises all trade state mutations.
# Both auto_signal_job and trade_monitor_job acquire this lock
# only for the brief critical section (state check + update),
# NOT during network I/O (API fetches / Telegram sends).
trade_lock: asyncio.Lock = asyncio.Lock()

# Heartbeat: auto_signal_job stamps last_cycle; trade_monitor_job stamps
# last_monitor. watchdog.py reads both to detect stuck loops.
heartbeat: dict = {"last_cycle": 0.0, "last_monitor": 0.0}
