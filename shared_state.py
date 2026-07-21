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

# Heartbeat: auto_signal_job stamps this after every full asset-check
# cycle. watchdog.py reads it to detect a stuck/silently-dead loop
# (e.g. every asset's API call has been failing for hours) without the
# two modules importing each other directly.
heartbeat: dict = {"last_cycle": 0.0}
