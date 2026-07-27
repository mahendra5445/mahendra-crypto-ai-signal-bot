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
    LIVE_PRICE_DRIFT_MAX,
    MAX_NEW_TRADES_PER_CYCLE,
    SIGNAL_COOLDOWN_SEC,
    SIGNAL_CYCLE_SEC,
    STARTUP_DELAY_SEC,
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
from trade_tracker import persist_snapshot, save_trade, snapshot_state

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

async def _check_asset(application, asset: str) -> bool:
    """Returns True if this asset opened a new trade this cycle."""
    cfg   = ASSETS[asset.lower()]
    label = cfg["label"]

    candles = await asyncio.to_thread(get_candles, asset)

    decimals = effective_decimals(asset, candles.get("price"))

    result = get_signal(
        candles["close"], candles["high"], candles["low"],
        candles["timeframes"], candles.get("volume"), candles.get("open"),
        decimals=decimals,
    )

    if result["signal"] == "NO TRADE":
        logger.info(f"[AUTO] {asset.upper()} → No Trade")
        return False

    signal_price = float(candles["price"])
    live_price = await asyncio.to_thread(get_latest_price, asset)
    if live_price is not None:
        # H-008: abort if live quote drifted too far from the scored bar
        if signal_price > 0:
            drift = abs(live_price - signal_price) / signal_price
            if drift > LIVE_PRICE_DRIFT_MAX:
                logger.info(
                    f"[AUTO] {asset.upper()} aborted — live drift {drift:.2%} "
                    f"> {LIVE_PRICE_DRIFT_MAX:.2%}"
                )
                return False
        decimals = effective_decimals(asset, live_price)
        result.update(calculate_trade(
            result["signal"], live_price, result.get("atr_value", 0),
            decimals=decimals, session_active=result.get("session_active", True),
        ))
        candles["price"] = live_price

    # M-019: unusable ATR → empty levels → do not open
    if result.get("entry") is None or result.get("sl") is None:
        logger.info(f"[AUTO] {asset.upper()} aborted — missing trade levels (ATR?)")
        return False

    message = format_signal(candles, result, decimals=decimals, label=label)

    # ── critical section: reserve intent, do NOT persist yet (C-003) ─────
    async with trade_lock:
        if _in_cooldown(asset):
            return False

        if message == _last_signal_msg.get(asset):
            logger.info(f"[AUTO] {asset.upper()} duplicate signal skipped")
            return False

        allowed, reason = can_open(asset)
        if not allowed:
            logger.info(f"[GUARD] {asset.upper()} signal blocked — {reason}")
            return False

        # Reserve dedup key so parallel work does not double-post; cleared on
        # notify failure.
        _last_signal_msg[asset] = message

    ok = await notify_channel(application, message)
    if not ok:
        async with trade_lock:
            _last_signal_msg[asset] = None
        logger.error(f"[AUTO] {asset.upper()} notify failed — trade NOT saved")
        return False

    # P-003: pre-notify guard decision is authoritative. After a successful
    # channel post, always save+track — re-checking can_open here created
    # orphan channel signals with no monitor.
    async with trade_lock:
        trade = save_trade(result, asset=asset, persist=False)
        if trade is None:
            logger.error(f"[AUTO] {asset.upper()} save_trade rejected after notify")
            return False
        snap_trades, snap_id = snapshot_state()
        _last_signal_time[asset] = time.monotonic()

    persisted = await asyncio.to_thread(persist_snapshot, snap_trades, snap_id)
    if not persisted:
        # P-002: in-memory trade exists but disk/Postgres write failed.
        logger.critical(
            f"[AUTO] {asset.upper()} #{trade['id']} posted but NOT persisted — "
            "restart will lose this open trade until Postgres is healthy"
        )
        await notify_channel(
            application,
            f"⚠️ PERSISTENCE FAILURE\n\n"
            f"{asset.upper()} signal was posted but trade state did not save.\n"
            f"Check DATABASE_URL / Postgres immediately.",
        )
        return False

    logger.info(f"[AUTO] {asset.upper()} signal posted to channel and tracked")
    return True


# ── main job loop ─────────────────────────────────────────────────────────

async def auto_signal_job(application) -> None:
    logger.info(f"[AUTO] Signal job started — cycle {SIGNAL_CYCLE_SEC}s")
    heartbeat["last_cycle"] = time.time()

    if STARTUP_DELAY_SEC > 0:
        logger.info(f"[AUTO] Warm-up — first scan in {STARTUP_DELAY_SEC}s")
        heartbeat["last_cycle"] = time.time()
        await asyncio.sleep(STARTUP_DELAY_SEC)

    while True:
        try:
            if await asyncio.to_thread(is_high_impact_news):
                logger.info("[NEWS FILTER] High-impact USD news — signals paused 5 min")
                heartbeat["last_cycle"] = time.time()
                await asyncio.sleep(300)
                continue
        except Exception as e:
            logger.error(f"[AUTO] News check failed: {e}")

        opened = 0
        for asset in ASSET_LIST:
            try:
                if await _check_asset(application, asset):
                    opened += 1
                    if opened >= MAX_NEW_TRADES_PER_CYCLE:
                        logger.info(
                            f"[AUTO] Per-cycle cap reached "
                            f"({opened}/{MAX_NEW_TRADES_PER_CYCLE}) — "
                            f"remaining coins wait for the next scan"
                        )
                        break
            except Exception as e:
                logger.error(f"[AUTO] {asset.upper()} error: {e}")

        heartbeat["last_cycle"] = time.time()
        await asyncio.sleep(SIGNAL_CYCLE_SEC)
