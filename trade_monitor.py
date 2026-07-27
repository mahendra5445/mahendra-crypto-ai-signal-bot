"""
Trade Monitor Job — walks each open trade forward through the 1-minute bars
that happened AFTER it was opened, and fires SL / TP1 / TP2 / TP3 in the
order the market actually produced them.
"""

import asyncio
import logging
import time

from config import MONITOR_INTERVAL_SEC, effective_decimals
from data import get_recent_bars
from notify import notify_channel
from shared_state import heartbeat, trade_lock
from trade_tracker import (
    get_open_trades,
    mark_tp1_hit,
    mark_tp2_hit,
    persist_snapshot,
    snapshot_state,
    update_trade,
)

logger = logging.getLogger(__name__)

CHECK_INTERVAL = MONITOR_INTERVAL_SEC
SL_BUFFER_PCT = 0.0003
BAR_LOOKBACK = 20


def bars_usable_after_entry(bars: list[dict], opened_ts: float | None) -> list[dict]:
    """
    Filter 1m bars that are relevant after trade entry (P-004).

    Yahoo 1m bar timestamps are candle OPEN times. Flooring opened_ts to the
    containing minute includes the in-progress entry candle without replaying
    earlier full minutes (which caused false SL hits historically).
    """
    if not opened_ts:
        return bars[-1:] if bars else []
    entry_bar_open = int(opened_ts) - (int(opened_ts) % 60)
    return [b for b in bars if b["ts"] >= entry_bar_open]


def _events_for_bar(trade: dict, bar: dict) -> list[str]:
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
        return ["be" if trade["hit_tp1"] else "sl"]

    return tps


async def _check_trade(application, trade: dict, bars: list[dict]) -> None:
    notifications: list[str] = []
    need_persist = False

    decimals = effective_decimals(trade["asset"], trade.get("entry"))

    async with trade_lock:
        if trade["status"] != "OPEN":
            return

        usable = bars_usable_after_entry(bars, trade.get("opened_ts"))

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
                    if update_trade(trade["id"], status, exit_price=bar["close"], persist=False):
                        need_persist = True
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
                    if mark_tp1_hit(trade, persist=False):
                        need_persist = True
                        notifications.append(
                            f"🎯 TP1 HIT\n\n"
                            f"#{trade['id']} | {trade['asset'].upper()} | {trade['signal']}\n"
                            f"Entry : {trade['entry']}\n"
                            f"TP1   : {trade['tp1']}\n"
                            f"Price : {price_display}\n\n"
                            f"✅ SL moved to Breakeven"
                        )

                elif level == "tp2":
                    if mark_tp2_hit(trade, persist=False):
                        need_persist = True
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
                    if update_trade(trade["id"], "TP", exit_price=trade["tp3"], persist=False):
                        need_persist = True
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

        snap = snapshot_state() if need_persist else None

    if need_persist and snap is not None:
        ok = await asyncio.to_thread(persist_snapshot, snap[0], snap[1])
        if not ok:
            logger.critical(
                f"[MONITOR] Persist failed after updating #{trade.get('id')} — "
                "open/closed state may be lost on restart"
            )
            notifications.append(
                "⚠️ PERSISTENCE FAILURE\n\n"
                "Trade monitor updated in-memory state but could not save.\n"
                "Check DATABASE_URL / Postgres immediately."
            )

    for msg in notifications:
        await notify_channel(application, msg)


async def trade_monitor_job(application) -> None:
    logger.info(f"[MONITOR] Started — polling every {CHECK_INTERVAL}s")
    heartbeat["last_monitor"] = time.time()
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

        heartbeat["last_monitor"] = time.time()
        await asyncio.sleep(CHECK_INTERVAL)
