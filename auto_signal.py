"""
Auto Signal Job

Fixes applied:
  #1  Duplicate save bug    — save_trade() now called AFTER all duplicate /
                              cooldown / open-trade checks pass.
  #2  Open trade lock       — has_open_trade() prevents stacking trades on the
                              same asset.
  #7  Race condition        — trade_lock (asyncio.Lock) serialises the critical
                              check-and-save section; I/O (API fetch, Telegram
                              send) happens outside the lock.
 #10  Cooldown system       — per-asset time-based cooldown (SIGNAL_COOLDOWN_SEC)
                              replaces brittle message-string comparison.

Clone note: signals are posted to a fixed Telegram channel (config.CHANNEL_ID,
see notify.py) instead of broadcasting to a list of registered users.
"""

import asyncio
import logging
import time

from config import ASSETS, ASSET_LIST
from data import get_candles, get_latest_price
from formatter import format_signal
from news import is_high_impact_news
from notify import notify_channel
from risk import calculate_trade
from shared_state import trade_lock, heartbeat
from strategy import get_signal
from trade_tracker import has_open_trade, save_trade

logger = logging.getLogger(__name__)

# ── per-asset cooldown ────────────────────────────────────────────────────
# After a signal fires we ignore further signals for this asset until
# SIGNAL_COOLDOWN_SEC seconds have passed.  This is independent of the
# 30-min polling cycle and survives message-string changes (price drift).
SIGNAL_COOLDOWN_SEC: int = 15 * 60   # 15 minutes (relaxed from 25 for more frequent signals)

_last_signal_time: dict[str, float] = {}   # asset -> unix timestamp of last sent signal
_last_signal_msg:  dict[str, str | None] = {a: None for a in ASSET_LIST}


def _in_cooldown(asset: str) -> bool:
    # BUG FIX: pehle default 0.0 tha aur time.monotonic() se compare hota tha.
    # time.monotonic() ka starting point arbitrary hota hai (Linux pe system
    # uptime). Agar server/container abhi restart hua ho (uptime < 25 min,
    # jo Render pe har deploy pe hota hai) to elapsed chhota nikalta tha aur
    # PEHLA signal hi galat cooldown mein block ho jaata tha.
    last = _last_signal_time.get(asset)
    if last is None:
        return False
    elapsed   = time.monotonic() - last
    remaining = SIGNAL_COOLDOWN_SEC - elapsed
    if remaining > 0:
        logger.info(
            f"[COOLDOWN] {asset.upper()} — "
            f"{int(remaining // 60)}m {int(remaining % 60)}s remaining"
        )
        return True
    return False


# ── per-asset check ───────────────────────────────────────────────────────

async def _check_asset(application, asset: str) -> None:

    cfg      = ASSETS[asset.lower()]
    decimals = cfg["decimals"]
    label    = cfg["label"]

    # ── 1. Fetch data (outside lock — network I/O can be slow) ───────────
    # BUG FIX (event-loop block): get_candles() does blocking requests.get()
    # calls with up to 3 retries and time.sleep(1)/time.sleep(2) between
    # them, across 3 timeframes. Calling it directly here ran that
    # synchronously ON the asyncio event loop — so a slow/failing Yahoo
    # response froze the ENTIRE bot (every Telegram command, trade_monitor's
    # SL/TP checks, watchdog heartbeat) for as long as the retries took.
    # asyncio.to_thread() runs it on a worker thread instead, so the event
    # loop stays responsive to everything else while this waits on network I/O.
    candles = await asyncio.to_thread(get_candles, asset)
    result  = get_signal(
        candles["close"],
        candles["high"],
        candles["low"],
        candles["timeframes"],
        candles.get("volume"),
        candles.get("open"),
        decimals=decimals,
    )

    if result["signal"] == "NO TRADE":
        logger.info(f"[AUTO] {asset.upper()} → No Trade")
        return

    # BUG FIX: `result["entry"]` (and therefore SL/TP1/TP2/TP3) was
    # calculated off `candles["price"]`, which is the close of the last
    # FULLY CLOSED 5-minute candle. That price can already be several
    # minutes old by the time the signal is posted, so it routinely
    # differed from the live MT5 price — and if gold moved during that
    # gap, the trade could already be much closer to (or even past) its
    # SL the moment it "opened". Here we pull one more near-live price
    # and re-run calculate_trade() with it, so the posted entry matches
    # what you'd actually see on MT5 at signal time. If the live fetch
    # fails for any reason we just keep the candle-based levels instead
    # of blocking the signal.
    live_price = await asyncio.to_thread(get_latest_price, asset)
    if live_price is not None:
        fresh_levels = calculate_trade(
            result["signal"], live_price, result.get("atr_value", 0),
            decimals=decimals, session_active=result.get("session_active", True),
        )
        result.update(fresh_levels)
        candles["price"] = live_price

    # Build message outside lock (pure CPU, no I/O)
    message = format_signal(candles, result, decimals=decimals, label=label)

    # ── 2. Critical section (hold lock only for state checks + write) ─────
    async with trade_lock:

        # Cooldown check
        if _in_cooldown(asset):
            return

        # Exact duplicate message check (same price, same signal)
        if message == _last_signal_msg.get(asset):
            logger.info(f"[AUTO] {asset.upper()} duplicate signal skipped")
            return

        # One open trade per asset at a time
        if has_open_trade(asset):
            logger.info(
                f"[AUTO] {asset.upper()} already has an open trade — "
                f"new signal skipped"
            )
            return

        # All checks passed → persist trade and update cooldown atomically
        save_trade(result, asset=asset)
        _last_signal_msg[asset]  = message
        _last_signal_time[asset] = time.monotonic()

    # ── 3. Post to channel (outside lock — I/O) ────────────────────────────
    await notify_channel(application, message)
    logger.info(f"[AUTO] {asset.upper()} signal posted to channel")


# ── main job loop ─────────────────────────────────────────────────────────

async def auto_signal_job(application) -> None:
    logger.info("[AUTO] Signal job started")
    heartbeat["last_cycle"] = time.time()   # baseline so watchdog doesn't false-alarm at boot
    while True:
        # News filter
        # BUG FIX (event-loop block, missed in first pass): is_high_impact_news()
        # does a blocking requests.get() too — same issue as get_candles/
        # get_latest_price above. Wrapped in asyncio.to_thread() so a slow
        # or hanging news-feed request can't freeze the whole bot.
        try:
            if await asyncio.to_thread(is_high_impact_news):
                logger.info("[NEWS FILTER] High-impact USD news — signals paused 5 min")
                # Still stamp the heartbeat — this is an intentional pause,
                # not a stuck loop. A news window can span up to ~60 minutes
                # (30 min before + 30 min after the event), which is longer
                # than watchdog's 40-min stale threshold — without this the
                # watchdog would fire a false "stuck" alert during a
                # perfectly normal news pause.
                heartbeat["last_cycle"] = time.time()
                await asyncio.sleep(300)
                continue
        except Exception as e:
            logger.error(f"[AUTO] News check failed: {e}")

        # Check each asset independently so one failure can't block the other
        for asset in ASSET_LIST:
            try:
                await _check_asset(application, asset)
            except Exception as e:
                logger.error(f"[AUTO] {asset.upper()} error: {e}")

        # Heartbeat — watchdog.py uses this to detect a stuck/dead loop.
        # Stamped even if every asset above failed, since the loop itself
        # is still alive; watchdog cares about the *process* being stuck,
        # not about individual asset failures (those already log their own
        # errors above and self-recover next cycle).
        heartbeat["last_cycle"] = time.time()

        await asyncio.sleep(900)   # 15-minute cycle (relaxed from 30 for more frequent checks)
