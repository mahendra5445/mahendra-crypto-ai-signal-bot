"""
Auto Signal Job — scans every configured asset on a fixed cycle and posts
qualifying setups to the channel.

Ordering rule that keeps this correct: all network I/O (candles, live price,
news, Telegram) happens OUTSIDE the trade lock; the lock is held only for
the check-and-save decision. Anything else either freezes the event loop or
opens a race between two coroutines both deciding to open the same trade.
"""

import asyncio
import logging
import time

from config import (
    ASSETS,
    ASSET_LIST,
    SIGNAL_COOLDOWN_SEC,
    SIGNAL_CYCLE_SEC,
    effective_decimals,
)
from data import get_candles, get_latest_price
from formatter import format_signal
from guards import can_open
from news import is_high_impact_news
from notify import notify_channel
from risk import calculate_trade
from shared_state import heartbeat, trade_lock
from strategy import get_signal
from trade_tracker import save_trade

logger = logging.getLogger(__name__)

_last_signal_time: dict[str, float] = {}
_last_signal_msg:  dict[str, str | None] = {a: None for a in ASSET_LIST}


def _in_cooldown(asset: str) -> bool:
    last = _last_signal_time.get(asset)
    if last is None:
        return False
    remaining = SIGNAL_COOLDOWN_SEC - (time.monotonic() - last)
    if remaining > 0:
        logger.info(
            f"[COOLDOWN] {asset.upper()} — "
            f"{int(remaining // 60)}m {int(remaining % 60)}s remaining"
        )
        return True
    return False


# ── per-asset check ───────────────────────────────────────────────────────

async def _check_asset(application, asset: str) -> None:
    cfg   = ASSETS[asset.lower()]
    label = cfg["label"]

    # get_candles() does blocking HTTP with retries across 3 timeframes.
    # Running it on the event loop freezes every command, the monitor and
    # the watchdog for as long as it takes.
    candles = await asyncio.to_thread(get_candles, asset)

    # Precision derived from the live price, not a hardcoded per-coin number.
    decimals = effective_decimals(asset, candles.get("price"))

    result = get_signal(
        candles["close"], candles["high"], candles["low"],
        candles["timeframes"], candles.get("volume"), candles.get("open"),
        decimals=decimals,
    )

    if result["signal"] == "NO TRADE":
        logger.info(f"[AUTO] {asset.upper()} → No Trade")
        return

    # The strategy's entry is the close of the last fully closed 5-minute
    # candle, which can already be minutes stale. Re-price off a near-live
    # quote so the posted entry matches what the user actually sees.
    live_price = await asyncio.to_thread(get_latest_price, asset)
    if live_price is not None:
        decimals = effective_decimals(asset, live_price)
        result.update(calculate_trade(
            result["signal"], live_price, result.get("atr_value", 0),
            decimals=decimals, session_active=result.get("session_active", True),
        ))
        candles["price"] = live_price

    message = format_signal(candles, result, decimals=decimals, label=label)

    # ── critical section ──────────────────────────────────────────────────
    async with trade_lock:
        if _in_cooldown(asset):
            return

        if message == _last_signal_msg.get(asset):
            logger.info(f"[AUTO] {asset.upper()} duplicate signal skipped")
            return

        allowed, reason = can_open(asset)
        if not allowed:
            logger.info(f"[GUARD] {asset.upper()} signal blocked — {reason}")
            return

        save_trade(result, asset=asset)
        _last_signal_msg[asset]  = message
        _last_signal_time[asset] = time.monotonic()

    await notify_channel(application, message)
    logger.info(f"[AUTO] {asset.upper()} signal posted to channel")


# ── main job loop ─────────────────────────────────────────────────────────

async def auto_signal_job(application) -> None:
    logger.info(f"[AUTO] Signal job started — cycle {SIGNAL_CYCLE_SEC}s")
    heartbeat["last_cycle"] = time.time()

    while True:
        try:
            if await asyncio.to_thread(is_high_impact_news):
                logger.info("[NEWS FILTER] High-impact USD news — signals paused 5 min")
                # Stamp the heartbeat: this is an intentional pause, not a
                # stuck loop, and a news window can outlast the watchdog's
                # stale threshold.
                heartbeat["last_cycle"] = time.time()
                await asyncio.sleep(300)
                continue
        except Exception as e:
            logger.error(f"[AUTO] News check failed: {e}")

        for asset in ASSET_LIST:
            try:
                await _check_asset(application, asset)
            except Exception as e:
                logger.error(f"[AUTO] {asset.upper()} error: {e}")

        heartbeat["last_cycle"] = time.time()
        await asyncio.sleep(SIGNAL_CYCLE_SEC)
