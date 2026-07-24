"""
Trade Monitor Job — walks each open trade forward through the 1-minute bars
that happened AFTER it was opened, and fires SL / TP1 / TP2 / TP3 in the
order the market actually produced them.

Two structural bugs are fixed here, and they were the biggest single cause
of the destroyed win rate:

  A. PRE-ENTRY BARS.  The old version asked for "the last 3 bars" with no
     timestamps and evaluated them against a trade that may have opened
     seconds ago. A brand-new trade was therefore judged on three minutes
     of price action from BEFORE its own entry, so a large share of trades
     were closed — as SL, or as a fake TP1 that instantly moved the stop to
     breakeven — on their very first poll. Now every bar that started
     before the entry timestamp is discarded.

  B. MERGED WINDOW.  The old version collapsed 3 bars into one high/low
     pair and checked SL first, so any window where both the stop and the
     target were touched became a loss regardless of which came first.
     Now bars are replayed one at a time in chronological order, so a TP1
     in minute 1 followed by a stop in minute 3 is correctly a breakeven,
     not a loss. Within a SINGLE bar the order is genuinely unknowable from
     OHLC, so we stay pessimistic and take the stop — the standard,
     defensible convention.
"""

import asyncio
import logging

from config import MONITOR_INTERVAL_SEC, effective_decimals
from data import get_recent_bars
from notify import notify_channel
from shared_state import trade_lock
from trade_tracker import (
    get_open_trades,
    mark_tp1_hit,
    mark_tp2_hit,
    update_trade,
)

logger = logging.getLogger(__name__)

CHECK_INTERVAL = MONITOR_INTERVAL_SEC

# Small margin beyond the SL required before a close is confirmed, so a
# single glitched Yahoo print can't stop out a trade on a price that never
# really traded. 0.03% of price is far smaller than the ATR-based stop.
SL_BUFFER_PCT = 0.0003

# How many bars back to request. The monitor polls every 60s, so ~20 bars is
# a generous overlap that survives a few failed cycles without leaving a gap.
BAR_LOOKBACK = 20


# ── event detection for ONE bar ───────────────────────────────────────────

def _events_for_bar(trade: dict, bar: dict) -> list[str]:
    """
    Events triggered by a single 1-minute bar, computed against the trade's
    CURRENT state so levels already hit are excluded.

    Returns at most one closing event, and it is always last in the list.
    """
    is_buy = trade["signal"] == "BUY"
    high, low = bar["high"], bar["low"]

    adverse    = low  if is_buy else high
    favourable = high if is_buy else low

    sl_buffer = abs(trade["sl"]) * SL_BUFFER_PCT
    sl_hit = (
        (is_buy     and adverse <= trade["sl"] - sl_buffer) or
        (not is_buy and adverse >= trade["sl"] + sl_buffer)
    )

    def _reached(level_key: str) -> bool:
        lvl = trade[level_key]
        return (is_buy and favourable >= lvl) or (not is_buy and favourable <= lvl)

    tps: list[str] = []
    if not trade["hit_tp1"] and _reached("tp1"):
        tps.append("tp1")
    if not trade["hit_tp2"] and _reached("tp2"):
        tps.append("tp2")
    if not trade["hit_tp3"] and _reached("tp3"):
        tps.append("tp3")

    if sl_hit:
        # Same bar, both sides touched: we cannot know the order from OHLC.
        # Stay pessimistic and take the stop — but only after crediting any
        # TP that is BEYOND the stop in the favourable direction is not
        # possible, so we simply close here.
        return ["be" if trade["hit_tp1"] else "sl"]

    return tps


# ── per-trade replay ──────────────────────────────────────────────────────

async def _check_trade(application, trade: dict, bars: list[dict]) -> None:
    notifications: list[str] = []

    decimals = effective_decimals(trade["asset"], trade.get("entry"))

    async with trade_lock:
        # Re-check inside the lock — another coroutine may have closed it.
        if trade["status"] != "OPEN":
            return

        opened_ts = trade.get("opened_ts")

        # Only bars that STARTED at or after the entry. A bar that was
        # already in progress when the trade opened contains pre-entry
        # price action, so it is dropped too.
        if opened_ts:
            usable = [b for b in bars if b["ts"] >= opened_ts]
        else:
            # Legacy trade saved before opened_ts existed — we cannot prove
            # any bar is post-entry, so use only the newest bar rather than
            # replaying unknown history against it.
            usable = bars[-1:]

        if not usable:
            return

        for bar in usable:
            events = _events_for_bar(trade, bar)
            if not events:
                continue

            price_display = round(bar["close"], decimals)
            closed = False

            for level in events:
                if level in ("sl", "be"):
                    status = "SL" if level == "sl" else "BE"
                    if update_trade(trade["id"], status, exit_price=bar["close"]):
                        if level == "sl":
                            notifications.append(
                                f"🛑 SL HIT\n\n"
                                f"#{trade['id']} | {trade['asset'].upper()} | {trade['signal']}\n"
                                f"Entry : {trade['entry']}\n"
                                f"SL    : {trade.get('original_sl', trade['sl'])}\n"
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
                    closed = True
                    break

                if level == "tp1":
                    mark_tp1_hit(trade)   # keeps original_sl, moves sl to entry
                    notifications.append(
                        f"🎯 TP1 HIT\n\n"
                        f"#{trade['id']} | {trade['asset'].upper()} | {trade['signal']}\n"
                        f"Entry : {trade['entry']}\n"
                        f"TP1   : {trade['tp1']}\n"
                        f"Price : {price_display}\n\n"
                        f"✅ SL moved to Breakeven"
                    )

                elif level == "tp2":
                    mark_tp2_hit(trade)
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
                    if update_trade(trade["id"], "TP", exit_price=trade["tp3"]):
                        notifications.append(
                            f"🎯🎯🎯 TP3 HIT — FULL TARGET\n\n"
                            f"#{trade['id']} | {trade['asset'].upper()} | {trade['signal']}\n"
                            f"Entry : {trade['entry']}\n"
                            f"TP3   : {trade['tp3']}\n"
                            f"Price : {price_display}\n\n"
                            f"✅ Trade Closed — Full Target Hit 🏆"
                        )
                    closed = True
                    break

            if closed:
                break

    # Telegram I/O happens outside the lock
    for msg in notifications:
        await notify_channel(application, msg)


# ── main job loop ─────────────────────────────────────────────────────────

async def trade_monitor_job(application) -> None:
    logger.info(f"[MONITOR] Started — polling every {CHECK_INTERVAL}s")
    while True:
        try:
            open_trades = get_open_trades()

            if open_trades:
                asset_list = list({t["asset"] for t in open_trades})

                fetched = await asyncio.gather(
                    *(asyncio.to_thread(get_recent_bars, a, BAR_LOOKBACK) for a in asset_list),
                    return_exceptions=True,
                )

                bars_by_asset: dict[str, list[dict] | None] = {}
                for a, result in zip(asset_list, fetched):
                    if isinstance(result, Exception):
                        logger.error(f"[MONITOR] Bar fetch failed for {a.upper()}: {result}")
                        bars_by_asset[a] = None
                    else:
                        bars_by_asset[a] = result

                for trade in list(open_trades):
                    bars = bars_by_asset.get(trade["asset"])
                    if not bars:
                        logger.warning(
                            f"[MONITOR] No bars for {trade['asset'].upper()} — skipping"
                        )
                        continue
                    await _check_trade(application, trade, bars)

        except Exception as e:
            logger.error(f"[MONITOR ERROR] {e}")

        await asyncio.sleep(CHECK_INTERVAL)
