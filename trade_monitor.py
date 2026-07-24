"""
Trade Monitor Job

Fixes applied:
  #4  TP1/BE persistence   — mark_tp1_hit() / mark_tp2_hit() now persist the
                             updated trade dict to disk immediately.
  #7  Race condition       — trade_lock acquired during state mutations; Telegram
                             sends happen outside the lock.
 #12  Crash recovery       — open trades survive restart via JSON persistence.
"""

import asyncio
import logging

from config import ASSETS
from data import get_recent_range
from notify import notify_channel
from shared_state import trade_lock
from trade_tracker import (
    get_open_trades,
    mark_tp1_hit,
    mark_tp2_hit,
    update_trade,
)

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 60    # seconds. 60s poll + last-3-bars lookback = overlap,
                       # so no minute can slip between two checks unseen.


# ── helpers ───────────────────────────────────────────────────────────────

async def _notify_all(application, text: str) -> None:
    await notify_channel(application, text)


SL_BUFFER_PCT = 0.0003   # 0.03% of price — small margin beyond SL required
                          # before a close is confirmed, so a single noisy
                          # Yahoo tick (get_latest_price already median-filters
                          # 1-min closes, but a real live-quote glitch can
                          # still slip through) can't trigger a false SL
                          # close on a price that immediately reverts.


def _compute_events(trade: dict, high: float, low: float) -> list[str]:
    """
    Returns a list of event strings that have just been triggered at
    this price, in priority order (SL/BE first, then TP1→TP2→TP3).
    The list is computed against the trade's CURRENT state so that
    levels already hit are correctly excluded.
    """
    is_buy = trade["signal"] == "BUY"
    events: list[str] = []

    # ── Stop Loss / Breakeven ─────────────────────────────────────────────
    # BUG FIX: SL used to trigger on the exact touch of the level, which
    # meant a single spiky/glitchy price tick (Yahoo spot data is noisy
    # enough that data.py has its own _YahooGlitch handling for it) could
    # close a trade even if price immediately came back. Requiring price to
    # clear the SL by a small buffer filters that out without meaningfully
    # widening the real stop (it's ~0.03% of price, far smaller than the
    # ATR-based SL distance itself).
    # BUY ke liye khilaaf ka extreme = LOW, favour ka = HIGH. SELL ulta.
    adverse    = low  if is_buy else high
    favourable = high if is_buy else low

    sl_buffer = trade["sl"] * SL_BUFFER_PCT
    sl_hit = (
        (is_buy     and adverse <= trade["sl"] - sl_buffer) or
        (not is_buy and adverse >= trade["sl"] + sl_buffer)
    )
    if sl_hit:
        # If TP1 was already hit, SL at entry = breakeven close
        events.append("be" if trade["hit_tp1"] else "sl")
        return events   # trade closes here — no TPs to check

    # ── Take Profits ──────────────────────────────────────────────────────
    if not trade["hit_tp1"]:
        if (is_buy and favourable >= trade["tp1"]) or (not is_buy and favourable <= trade["tp1"]):
            events.append("tp1")

    if not trade["hit_tp2"]:
        if (is_buy and favourable >= trade["tp2"]) or (not is_buy and favourable <= trade["tp2"]):
            events.append("tp2")

    if not trade["hit_tp3"]:
        if (is_buy and favourable >= trade["tp3"]) or (not is_buy and favourable <= trade["tp3"]):
            events.append("tp3")

    return events


# ── per-trade check ───────────────────────────────────────────────────────

async def _check_trade(application, trade: dict, bar: dict) -> None:
    notifications: list[str] = []
    trade_closed = False

    decimals = ASSETS.get(trade["asset"].lower(), {}).get("decimals", 2)
    price_display = round(bar["close"], decimals)

    # ── Critical section: mutate state atomically ─────────────────────────
    async with trade_lock:
        # Re-check status inside lock (another coroutine may have closed
        # this trade between the price fetch and acquiring the lock)
        if trade["status"] != "OPEN":
            return

        # BUG FIX: events pehle lock ke BAHAR compute hote the — us waqt ka
        # trade state (hit_tp1, sl) purana ho sakta tha. Example: TP1 abhi
        # hit hua aur SL entry pe move hua, lekin purane state se compute
        # kiya "sl" event ab bhi purane SL pe fire karke trade ko galat
        # "SL Hit" mark kar deta. Ab events lock ke andar fresh state se
        # compute hote hain.
        events = _compute_events(trade, bar["high"], bar["low"])
        if not events:
            return

        for level in events:
            if level in ("sl", "be"):
                status = "SL" if level == "sl" else "BE"
                if update_trade(trade["id"], status):
                    if level == "sl":
                        notifications.append(
                            f"🛑 SL HIT\n\n"
                            f"#{trade['id']} | {trade['asset'].upper()} | {trade['signal']}\n"
                            f"Entry : {trade['entry']}\n"
                            f"SL    : {trade['sl']}\n"
                            f"Price : {price_display}\n\n"
                            f"Trade Closed ❌"
                        )
                    else:
                        notifications.append(
                            f"⚪ BREAKEVEN\n\n"
                            f"#{trade['id']} | {trade['asset'].upper()} | {trade['signal']}\n"
                            f"Entry : {trade['entry']}\n"
                            f"Price : {price_display}\n\n"
                            f"Closed at Breakeven — TP1 was already secured ✅"
                        )
                trade_closed = True
                break   # trade closed — ignore remaining events

            elif level == "tp1":
                mark_tp1_hit(trade)   # sets hit_tp1=True, sl=entry, persists
                notifications.append(
                    f"🎯 TP1 HIT\n\n"
                    f"#{trade['id']} | {trade['asset'].upper()} | {trade['signal']}\n"
                    f"Entry : {trade['entry']}\n"
                    f"TP1   : {trade['tp1']}\n"
                    f"Price : {price_display}\n\n"
                    f"✅ SL moved to Breakeven"
                )

            elif level == "tp2":
                mark_tp2_hit(trade)   # persists
                notifications.append(
                    f"🎯🎯 TP2 HIT\n\n"
                    f"#{trade['id']} | {trade['asset'].upper()} | {trade['signal']}\n"
                    f"Entry : {trade['entry']}\n"
                    f"TP2   : {trade['tp2']}\n"
                    f"Price : {price_display}\n\n"
                    f"✅ Trail SL for remaining position"
                )

            elif level == "tp3":
                trade["hit_tp3"] = True
                if update_trade(trade["id"], "TP"):   # persists inside
                    notifications.append(
                        f"🎯🎯🎯 TP3 HIT — FULL TARGET\n\n"
                        f"#{trade['id']} | {trade['asset'].upper()} | {trade['signal']}\n"
                        f"Entry : {trade['entry']}\n"
                        f"TP3   : {trade['tp3']}\n"
                        f"Price : {price_display}\n\n"
                        f"✅ Trade Closed — Full Target Hit 🏆"
                    )
                trade_closed = True
                break   # trade closed — ignore remaining events

    # ── Send notifications outside lock ───────────────────────────────────
    for msg in notifications:
        await _notify_all(application, msg)


# ── main job loop ─────────────────────────────────────────────────────────

async def trade_monitor_job(application) -> None:
    logger.info("[MONITOR] Trade monitor started")
    while True:
        try:
            open_trades = get_open_trades()

            if open_trades:
                # Fetch prices for all needed assets in one batch
                # BUG FIX (event-loop block, missed in first pass): same
                # blocking-requests.get() issue as data.get_candles() — and
                # this one runs every CHECK_INTERVAL (2 min), more often
                # than any other job, so its freeze impact was actually the
                # worst of the three. asyncio.gather + to_thread runs all
                # needed price fetches concurrently on worker threads
                # instead of serially blocking the event loop.
                asset_list = list({t["asset"] for t in open_trades})
                # return_exceptions=True: without this, one asset raising
                # (e.g. an unexpected error inside get_latest_price) would
                # abort the whole gather() and skip SL/TP monitoring for
                # every OTHER open trade this cycle too, not just the
                # failing asset.
                fetched = await asyncio.gather(
                    *(asyncio.to_thread(get_recent_range, a) for a in asset_list),
                    return_exceptions=True,
                )
                prices: dict[str, dict | None] = {}
                for a, result in zip(asset_list, fetched):
                    if isinstance(result, Exception):
                        logger.error(f"[MONITOR] Price fetch failed for {a.upper()}: {result}")
                        prices[a] = None
                    else:
                        prices[a] = result

                # Iterate over a snapshot copy so mutations don't affect the loop
                for trade in list(open_trades):
                    bar = prices.get(trade["asset"])
                    if bar is None:
                        logger.warning(
                            f"[MONITOR] No price for {trade['asset'].upper()} — skipping"
                        )
                        continue
                    await _check_trade(application, trade, bar)

        except Exception as e:
            logger.error(f"[MONITOR ERROR] {e}")

        await asyncio.sleep(CHECK_INTERVAL)
