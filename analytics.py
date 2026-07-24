"""
Performance analytics in R (risk multiples).

Win rate on its own is close to meaningless for a bot that scales out of
positions: a 40% win rate at +1.85R beats a 70% win rate at +0.2R. These
functions answer the only question that matters — is the expectancy
positive — and they are computed from `original_sl`, so moving the stop to
breakeven no longer corrupts the arithmetic.

PARTIAL-EXIT MODEL (this is an assumption, and it is stated on the report
so nobody mistakes it for measured fill data):
    50% of the position closes at TP1, 25% at TP2, 25% at TP3.
Outcomes therefore settle at:
    SL, no TP1 hit  → -1.00 R
    BE after TP1    →  +0.60 R   (0.50 x 1.2R banked, remainder at entry)
    BE after TP2    →  +1.10 R   (0.50 x 1.2R + 0.25 x 2.0R)
    TP3 (full)      →  +1.85 R   (0.50 x 1.2R + 0.25 x 2.0R + 0.25 x 3.0R)
"""

from risk import TP1_R, TP2_R, TP3_R
from trade_tracker import all_trades

W1, W2, W3 = 0.50, 0.25, 0.25


def trade_r(trade: dict) -> float | None:
    """R-multiple of a closed trade, or None if it is still open."""
    if trade["status"] == "OPEN":
        return None

    if trade["status"] == "TP" or trade.get("hit_tp3"):
        return round(W1 * TP1_R + W2 * TP2_R + W3 * TP3_R, 4)

    if trade["status"] == "BE":
        if trade.get("hit_tp2"):
            return round(W1 * TP1_R + W2 * TP2_R, 4)
        if trade.get("hit_tp1"):
            return round(W1 * TP1_R, 4)
        return 0.0

    if trade["status"] == "SL":
        if trade.get("hit_tp2"):
            return round(W1 * TP1_R + W2 * TP2_R - W3 * 1.0, 4)
        if trade.get("hit_tp1"):
            return round(W1 * TP1_R - (W2 + W3) * 1.0, 4)
        return -1.0

    return None


def _closed(asset: str | None = None, since: str | None = None) -> list[dict]:
    trades = [t for t in all_trades() if t["status"] != "OPEN"]
    if asset:
        a = asset.lower()
        trades = [t for t in trades if t["asset"].lower() == a]
    if since:
        trades = [t for t in trades if t.get("time", "") >= since]
    trades.sort(key=lambda t: t.get("closed_ts") or 0)
    return trades


def performance(asset: str | None = None, since: str | None = None) -> dict:
    trades = _closed(asset, since)
    rs = [r for r in (trade_r(t) for t in trades) if r is not None]

    if not rs:
        return {"count": 0}

    wins   = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]

    gross_win  = sum(wins)
    gross_loss = abs(sum(losses))

    # Equity curve in R, for max drawdown
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for r in rs:
        equity += r
        peak    = max(peak, equity)
        max_dd  = max(max_dd, peak - equity)

    # Longest streaks
    best_win_streak = cur_win = 0
    best_loss_streak = cur_loss = 0
    for r in rs:
        if r > 0:
            cur_win += 1
            cur_loss = 0
        elif r < 0:
            cur_loss += 1
            cur_win = 0
        else:
            cur_win = cur_loss = 0
        best_win_streak  = max(best_win_streak, cur_win)
        best_loss_streak = max(best_loss_streak, cur_loss)

    return {
        "count":         len(rs),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(len(wins) / len(rs) * 100, 2),
        "total_r":       round(equity, 2),
        "expectancy_r":  round(equity / len(rs), 3),
        "avg_win_r":     round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss_r":    round(-gross_loss / len(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_dd_r":      round(max_dd, 2),
        "best_streak":   best_win_streak,
        "worst_streak":  best_loss_streak,
    }


def performance_text(asset: str | None = None, since: str | None = None) -> str:
    p = performance(asset, since)
    scope = asset.upper() if asset else "All Assets"

    if p["count"] == 0:
        return f"📈 PERFORMANCE — {scope}\n\nNo closed trades yet."

    pf = p["profit_factor"]
    pf_line = f"{pf}" if pf is not None else "∞ (no losses yet)"

    verdict = "✅ Positive edge" if p["expectancy_r"] > 0 else "⚠️ Negative expectancy"

    return (
        f"📈 PERFORMANCE — {scope}\n\n"
        f"Closed trades : {p['count']}\n"
        f"Win rate      : {p['win_rate']}%  ({p['wins']}W / {p['losses']}L)\n\n"
        f"Total         : {p['total_r']:+.2f} R\n"
        f"Expectancy    : {p['expectancy_r']:+.3f} R per trade\n"
        f"Avg win       : {p['avg_win_r']:+.2f} R\n"
        f"Avg loss      : {p['avg_loss_r']:+.2f} R\n"
        f"Profit factor : {pf_line}\n"
        f"Max drawdown  : -{p['max_dd_r']:.2f} R\n"
        f"Best streak   : {p['best_streak']}W\n"
        f"Worst streak  : {p['worst_streak']}L\n\n"
        f"{verdict}\n\n"
        f"R = one unit of risk. Assumes 50/25/25 scale-out at TP1/TP2/TP3."
    )
