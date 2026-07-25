"""
Risk guards — the circuit breakers that separate a signal bot from a
signal firehose.

Without these the bot will happily open twelve trades in a bad hour and
keep going after six stops in a row, which is exactly how a strategy with
a real edge still ends up net negative. Every limit is tunable from the
environment (see config.py).

All four checks are cheap and read from the live trade list, so they stay
correct across restarts.
"""

import logging
import time

from clock import today_str
from config import (
    LOSS_PAUSE_SEC,
    MAX_CONSEC_LOSSES,
    MAX_OPEN_TRADES,
    MAX_TRADES_PER_DAY,
)
from trade_tracker import all_trades, has_open_trade

logger = logging.getLogger(__name__)


def _closed_sorted() -> list[dict]:
    closed = [t for t in all_trades() if t["status"] != "OPEN"]
    closed.sort(key=lambda t: t.get("closed_ts") or 0)
    return closed


def consecutive_losses() -> int:
    """
    Losing streak counted backwards from the most recent close.
    A BE (breakeven after TP1) breaks the streak — the first partial was
    banked, so it is not a loss.
    """
    streak = 0
    for t in reversed(_closed_sorted()):
        if t["status"] == "SL" and not t.get("hit_tp1"):
            streak += 1
        else:
            break
    return streak


def pause_remaining_sec() -> int:
    """
    Seconds left on the losing-streak pause, 0 if not paused.
    Measured from the close of the most recent losing trade.
    """
    if consecutive_losses() < MAX_CONSEC_LOSSES:
        return 0

    closed = _closed_sorted()
    if not closed:
        return 0

    last_close = closed[-1].get("closed_ts") or 0
    elapsed = time.time() - last_close
    return max(0, int(LOSS_PAUSE_SEC - elapsed))


def trades_today() -> int:
    today = today_str()
    return sum(1 for t in all_trades() if str(t.get("time", "")).startswith(today))


def open_count() -> int:
    return sum(1 for t in all_trades() if t["status"] == "OPEN")


def can_open(asset: str) -> tuple[bool, str]:
    """
    (allowed, reason). Caller must hold shared_state.trade_lock so the
    check and the resulting save_trade() are one atomic decision.
    """
    if has_open_trade(asset):
        return False, f"{asset.upper()} already has an open trade"

    n_open = open_count()
    if n_open >= MAX_OPEN_TRADES:
        return False, f"max concurrent trades reached ({n_open}/{MAX_OPEN_TRADES})"

    n_today = trades_today()
    if n_today >= MAX_TRADES_PER_DAY:
        return False, f"daily trade cap reached ({n_today}/{MAX_TRADES_PER_DAY})"

    paused = pause_remaining_sec()
    if paused > 0:
        return False, (
            f"paused after {consecutive_losses()} consecutive losses — "
            f"{paused // 60}m remaining"
        )

    return True, "ok"


def status_text() -> str:
    """Human-readable guard state for the /guards command."""
    paused = pause_remaining_sec()
    lines = [
        "🛡 RISK GUARDS\n",
        f"Open trades   : {open_count()} / {MAX_OPEN_TRADES}",
        f"Trades today  : {trades_today()} / {MAX_TRADES_PER_DAY}",
        f"Loss streak   : {consecutive_losses()} / {MAX_CONSEC_LOSSES}",
    ]
    if paused > 0:
        lines.append(f"\n⛔ PAUSED — {paused // 3600}h {(paused % 3600) // 60}m remaining")
    else:
        lines.append("\n✅ Accepting new signals")
    return "\n".join(lines)
