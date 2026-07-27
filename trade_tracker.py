"""
Trade Tracker — the single source of truth for all trade state.

Every trade now records enough to be audited after the fact:
  opened_ts   unix timestamp of entry — the trade monitor uses this to
              ignore bars that predate the trade (see trade_monitor.py)
  original_sl the stop as it was at entry. mark_tp1_hit() moves `sl` to
              breakeven, which used to DESTROY the only record of the real
              risk distance, making every R-multiple in the stats fiction.
  exit_price / closed_ts  what actually closed the trade, and when.
"""

import copy
import logging
import time

from clock import fmt_ts, today_str
from persistence import load_trades_from_disk, save_trades_to_disk

logger = logging.getLogger(__name__)

MAX_TRADE_HISTORY = 500

_trades: list[dict]
_next_id: int
_trades, _next_id = load_trades_from_disk()


# ── internal helpers ──────────────────────────────────────────────────────

def _persist() -> bool:
    """Blocking persist — prefer persist_snapshot() from async paths (H-012)."""
    ok = save_trades_to_disk(_trades, _next_id)
    if not ok:
        logger.critical("[TRACKER] Persist failed — in-memory state is NOT durable")
    return ok


def snapshot_state() -> tuple[list, int]:
    """Deep copy of state for off-lock persistence."""
    return copy.deepcopy(_trades), _next_id


def persist_snapshot(trades: list, next_id: int) -> bool:
    """Write a previously taken snapshot (safe to call from a worker thread)."""
    ok = save_trades_to_disk(trades, next_id)
    if not ok:
        logger.critical("[TRACKER] Snapshot persist failed — state NOT durable")
    return ok


def _trim_history() -> None:
    """Evict old CLOSED trades past the cap. Open trades are never evicted."""
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

def all_trades() -> list[dict]:
    """Read-only view for analytics/guards."""
    return list(_trades)


def has_open_trade(asset: str) -> bool:
    a = asset.lower()
    return any(t["status"] == "OPEN" and t["asset"].lower() == a for t in _trades)


def save_trade(result: dict, asset: str = "btc", *, persist: bool = True) -> dict | None:
    """
    Persist a new trade from a signal result dict.
    Caller MUST hold shared_state.trade_lock.
    Set persist=False to mutate only; then call persist_snapshot(snapshot_state()).
    """
    global _next_id

    signal = result.get("signal")
    if signal not in ("BUY", "SELL"):
        return None
    if result.get("entry") is None or result.get("sl") is None:
        return None

    now = time.time()
    entry = float(result["entry"])
    sl    = float(result["sl"])
    day   = today_str()

    trade = {
        "id":          _next_id,
        "asset":       asset.lower(),
        "signal":      signal,
        "entry":       round(entry, 8),
        "sl":          round(sl, 8),
        "original_sl": round(sl, 8),
        "tp1":         round(float(result["tp1"]), 8),
        "tp2":         round(float(result["tp2"]), 8),
        "tp3":         round(float(result["tp3"]), 8),
        "hit_tp1":     False,
        "hit_tp2":     False,
        "hit_tp3":     False,
        "status":      "OPEN",
        "opened_ts":   now,
        "opened_day":  day,
        "closed_ts":   None,
        "exit_price":  None,
        "time":        fmt_ts(now),
    }

    _next_id += 1
    _trades.append(trade)
    _trim_history()
    if persist:
        _persist()

    logger.info(
        f"[TRADE] Saved #{trade['id']} {asset.upper()} {signal} "
        f"entry={trade['entry']} sl={trade['sl']}"
    )
    return trade


def get_open_trades() -> list[dict]:
    return [t for t in _trades if t["status"] == "OPEN"]


def update_trade(
    trade_id: int, status: str, exit_price: float | None = None, *, persist: bool = True
) -> bool:
    """
    Close a trade with a final status (TP / SL / BE).
    Caller MUST hold shared_state.trade_lock.
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
        trade["status"]     = status
        trade["closed_ts"]  = time.time()
        trade["exit_price"] = round(float(exit_price), 8) if exit_price is not None else None
        if persist:
            _persist()
        logger.info(f"[TRADE] #{trade_id} closed as {status}")
        return True

    logger.warning(f"[TRADE] #{trade_id} not found for update → {status}")
    return False


def mark_tp1_hit(trade: dict, *, persist: bool = True) -> bool:
    """
    Mark TP1 as hit and move the stop to breakeven.
    `original_sl` is preserved.
    """
    if trade.get("status") != "OPEN":
        return False
    if trade.get("hit_tp1"):
        return False
    trade["hit_tp1"] = True
    trade.setdefault("original_sl", trade["sl"])
    trade["sl"] = trade["entry"]
    if persist:
        _persist()
    logger.info(f"[TRADE] #{trade['id']} TP1 hit — SL moved to breakeven")
    return True


def mark_tp2_hit(trade: dict, *, persist: bool = True) -> bool:
    if trade.get("status") != "OPEN":
        return False
    trade["hit_tp2"] = True
    if persist:
        _persist()
    logger.info(f"[TRADE] #{trade['id']} TP2 hit")
    return True


def find_trade(trade_id: int) -> dict | None:
    for t in _trades:
        if t["id"] == trade_id:
            return t
    return None


def _filtered(asset: str | None = None, since: str | None = None) -> list[dict]:
    trades = _trades
    if asset:
        a = asset.lower()
        trades = [t for t in trades if t["asset"].lower() == a]
    if since:
        trades = [t for t in trades if t["time"] >= since]
    return trades


def get_stats(asset: str | None = None, since: str | None = None) -> dict:
    trades = _filtered(asset, since)

    buy  = sum(1 for t in trades if t["signal"] == "BUY")
    sell = sum(1 for t in trades if t["signal"] == "SELL")

    closed_trades = [t for t in trades if t["status"] != "OPEN"]
    tp   = sum(1 for t in closed_trades if t["status"] == "TP")
    sl   = sum(1 for t in closed_trades if t["status"] == "SL")
    be   = sum(1 for t in closed_trades if t["status"] == "BE")
    closed = len(closed_trades)

    wins   = sum(1 for t in closed_trades
                 if t["status"] == "TP" or (t["status"] == "BE" and t.get("hit_tp1")))
    losses = closed - wins

    win_rate = round((wins / closed) * 100, 2) if closed else 0.0

    return {
        "total":    buy + sell,
        "buy":      buy,
        "sell":     sell,
        "open":     sum(1 for t in trades if t["status"] == "OPEN"),
        "closed":   closed,
        "tp":       tp,
        "sl":       sl,
        "be":       be,
        "wins":     wins,
        "losses":   losses,
        "win_rate": win_rate,
    }


def get_last_trades(limit: int = 10, asset: str | None = None) -> list[dict]:
    trades = _filtered(asset)
    return list(reversed(trades[-limit:]))


def history_text(limit: int = 10, asset: str | None = None) -> str:
    trades = get_last_trades(limit, asset=asset)
    if not trades:
        return "❌ No trades available."

    status_icon = {"OPEN": "🔵", "TP": "✅", "SL": "🛑", "BE": "⚪"}
    lines = ["📜 LAST TRADES\n"]

    for trade in trades:
        icon = status_icon.get(trade["status"], "❓")
        orig = trade.get("original_sl", trade["sl"])

        if trade.get("hit_tp1") and trade["sl"] == trade["entry"]:
            sl_line = f"{trade['sl']}  (moved to BE, original {orig})"
        else:
            sl_line = f"{trade['sl']}"

        progress = ""
        if trade["hit_tp3"]:
            progress = "TP1 ✅ TP2 ✅ TP3 ✅"
        elif trade["hit_tp2"]:
            progress = "TP1 ✅ TP2 ✅"
        elif trade["hit_tp1"]:
            progress = "TP1 ✅"

        lines.append(
            f"#{trade['id']} | {trade['asset'].upper()} | "
            f"{trade['signal']} {icon}\n"
            f"Entry  : {trade['entry']}\n"
            f"SL     : {sl_line}\n"
            f"TP1    : {trade['tp1']}\n"
            f"TP2    : {trade['tp2']}\n"
            f"TP3    : {trade['tp3']}\n"
            f"Status : {trade['status']}"
            + (f"  ({progress})" if progress else "") + "\n"
            f"{trade['time']}\n"
        )

    return "\n".join(lines)
