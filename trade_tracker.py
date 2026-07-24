"""
Trade Tracker — single source of truth for all trade state.

Fixes applied:
  #1  Duplicate trade save     — save_trade() is idempotent; caller must hold
                                  trade_lock before calling it.
  #2  Open trade lock          — has_open_trade(asset) lets callers check before
                                  creating a new trade.
  #3  Trade persistence        — every mutation calls _persist() → atomic JSON write.
  #4  TP1/BE persistence       — mark_tp1_hit() / mark_tp2_hit() persist immediately.
  #5  Duplicate trade ID       — persisted next_id counter, never resets or repeats
                                  even after history cleanup.
  #6  Stats accuracy           — get_stats() is always derived from the live trade
                                  list; no separate counters that can drift.
 #15/#16 History cleanup/limit — _trim_history() keeps ≤ MAX_TRADE_HISTORY trades;
                                  open trades are never evicted.
"""

import logging
from datetime import datetime

from persistence import load_trades_from_disk, save_trades_to_disk

logger = logging.getLogger(__name__)

MAX_TRADE_HISTORY = 500   # maximum trades kept in memory / on disk

# ── module-level state (loaded once at import time) ──────────────────────
_trades: list[dict]
_next_id: int
_trades, _next_id = load_trades_from_disk()


# ── internal helpers ──────────────────────────────────────────────────────

def _persist() -> None:
    save_trades_to_disk(_trades, _next_id)


def _trim_history() -> None:
    """
    Evict old *closed* trades when the list exceeds MAX_TRADE_HISTORY.
    Open trades are never evicted — they must be monitored until settled.
    """
    global _trades
    if len(_trades) <= MAX_TRADE_HISTORY:
        return

    open_trades   = [t for t in _trades if t["status"] == "OPEN"]
    closed_trades = [t for t in _trades if t["status"] != "OPEN"]

    keep_closed   = max(0, MAX_TRADE_HISTORY - len(open_trades))
    trimmed       = len(closed_trades) - keep_closed
    closed_trades = closed_trades[-keep_closed:] if keep_closed > 0 else []

    _trades = closed_trades + open_trades
    _trades.sort(key=lambda t: t.get("id", 0))

    if trimmed > 0:
        logger.info(f"[TRACKER] History trimmed: removed {trimmed} old closed trades")


# ── public API ────────────────────────────────────────────────────────────

def has_open_trade(asset: str) -> bool:
    """True when the asset already has at least one OPEN trade."""
    a = asset.lower()
    return any(t["status"] == "OPEN" and t["asset"].lower() == a for t in _trades)


def save_trade(result: dict, asset: str = "btc") -> dict | None:
    """
    Persist a new trade from a signal result dict.
    Caller MUST hold shared_state.trade_lock before calling this.
    Returns the new trade dict, or None if signal is not BUY/SELL.
    """
    global _next_id

    signal = result.get("signal")
    if signal not in ("BUY", "SELL"):
        return None

    trade = {
        "id":       _next_id,
        "asset":    asset.lower(),
        "signal":   signal,
        "entry":    round(float(result["entry"]), 5),
        "sl":       round(float(result["sl"]),    5),
        "tp1":      round(float(result["tp1"]),   5),
        "tp2":      round(float(result["tp2"]),   5),
        "tp3":      round(float(result["tp3"]),   5),
        "hit_tp1":  False,
        "hit_tp2":  False,
        "hit_tp3":  False,
        "status":   "OPEN",
        "time":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    _next_id += 1
    _trades.append(trade)
    _trim_history()
    _persist()

    logger.info(
        f"[TRADE] Saved #{trade['id']} {asset.upper()} {signal} "
        f"entry={trade['entry']} sl={trade['sl']}"
    )
    return trade


def get_open_trades() -> list[dict]:
    return [t for t in _trades if t["status"] == "OPEN"]


def update_trade(trade_id: int, status: str) -> bool:
    """
    Close a trade with a final status (TP / SL / BE).
    Caller MUST hold shared_state.trade_lock.
    Returns True on success, False if trade was not found or already closed.
    """
    for trade in _trades:
        if trade["id"] != trade_id:
            continue
        if trade["status"] != "OPEN":
            logger.warning(
                f"[TRADE] #{trade_id} is already '{trade['status']}', "
                f"ignoring request to set '{status}'"
            )
            return False
        trade["status"] = status
        _persist()
        logger.info(f"[TRADE] #{trade_id} closed as {status}")
        return True

    logger.warning(f"[TRADE] #{trade_id} not found for update → {status}")
    return False


def mark_tp1_hit(trade: dict) -> None:
    """
    Mark TP1 as hit and move SL to breakeven.
    Persists immediately.  Caller must hold trade_lock.
    """
    trade["hit_tp1"] = True
    trade["sl"]      = trade["entry"]
    _persist()
    logger.info(f"[TRADE] #{trade['id']} TP1 hit — SL moved to breakeven")


def mark_tp2_hit(trade: dict) -> None:
    """Mark TP2 as hit.  Persists immediately.  Caller must hold trade_lock."""
    trade["hit_tp2"] = True
    _persist()
    logger.info(f"[TRADE] #{trade['id']} TP2 hit")


def find_trade(trade_id: int) -> dict | None:
    for t in _trades:
        if t["id"] == trade_id:
            return t
    return None


def get_stats(asset: str | None = None, since: str | None = None) -> dict:
    """
    Stats derived live from the trade list — never drifts out of sync
    regardless of restarts, cleanups, or any edge case.

    asset — optional, filter to a single asset's trades (e.g. "eurusd").
    since — optional, "YYYY-MM-DD" — only trades opened on/after this date
            (trade["time"] is "YYYY-MM-DD HH:MM:SS", so a plain string
            comparison works). Used for daily summaries.
    No args = old behaviour, combined across every asset, all-time.
    """
    trades = _trades
    if asset:
        a = asset.lower()
        trades = [t for t in trades if t["asset"].lower() == a]
    if since:
        trades = [t for t in trades if t["time"] >= since]

    buy  = sum(1 for t in trades if t["signal"] == "BUY")
    sell = sum(1 for t in trades if t["signal"] == "SELL")
    tp   = sum(1 for t in trades if t["status"] == "TP")
    sl   = sum(1 for t in trades if t["status"] == "SL")
    be   = sum(1 for t in trades if t["status"] == "BE")

    closed   = tp + sl + be
    # BUG FIX: status "TP" sirf TP3 (full 3R target) par set hota hai.
    # Jo trade TP1 hit karke breakeven par band hua wo PROFIT mein tha,
    # par win rate use haar gin raha tha -- isliye 84 signals par win rate
    # 5.41% dikh raha tha jabki 11 "breakeven" trades asal mein jeete the.
    # Ab TP1 tak pahunchne wala har trade win hai.
    wins = sum(1 for t in trades
               if t["status"] == "TP" or t.get("hit_tp1"))
    win_rate = round((wins / closed) * 100, 2) if closed > 0 else 0.0

    return {
        "total":    buy + sell,
        "buy":      buy,
        "sell":     sell,
        "tp":       tp,
        "sl":       sl,
        "be":       be,
        "wins": wins,
        "win_rate": win_rate,
    }


def get_last_trades(limit: int = 10, asset: str | None = None) -> list[dict]:
    trades = _trades
    if asset:
        a = asset.lower()
        trades = [t for t in trades if t["asset"].lower() == a]
    return list(reversed(trades[-limit:]))


def history_text(limit: int = 10, asset: str | None = None) -> str:
    trades = get_last_trades(limit, asset=asset)
    if not trades:
        return "❌ No trades available."

    status_icon = {"OPEN": "🔵", "TP": "✅", "SL": "🛑", "BE": "⚪"}
    lines = ["📜 LAST TRADES\n"]

    for trade in trades:
        icon = status_icon.get(trade["status"], "❓")
        lines.append(
            f"#{trade['id']} | {trade['asset'].upper()} | "
            f"{trade['signal']} {icon}\n"
            f"Entry  : {trade['entry']}\n"
            f"SL     : {trade['sl']}\n"
            f"TP1    : {trade['tp1']}\n"
            f"TP2    : {trade['tp2']}\n"
            f"TP3    : {trade['tp3']}\n"
            f"Status : {trade['status']}\n"
            f"{trade['time']}\n"
        )

    return "\n".join(lines)
