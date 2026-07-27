"""
Backtest engine.

Replays the REAL strategy and the REAL risk model over historical
1-minute data. Nothing here reimplements the bot's logic — it imports
strategy.get_signal(), risk.calculate_trade() and config.effective_decimals()
and drives them, so a passing backtest actually says something about the
code that runs live.

Usage:
    python backtest.py --days 90
    python backtest.py --days 30 --assets btc,eth,sol --fee-bps 20
    python backtest.py --days 90 --fee-bps 0        # frictionless, for comparison

WHAT IT SIMULATES FAITHFULLY
  - the 15-minute scan cycle and per-asset cooldown
  - one open trade per asset at a time
  - entry at the close of the 5-minute bar the signal fired on
  - SL/TP resolution on 1-minute bars, replayed in order, with the same
    pessimistic same-bar convention the live monitor uses
  - the session filter, driven by each BAR's timestamp rather than the
    wall clock

WHAT IT DOES NOT SIMULATE
  - book-level risk guards (MAX_OPEN_TRADES, MAX_TRADES_PER_DAY,
    consecutive-loss pause) — those are live-only in guards.py
  - the news filter (needs a live feed)
  - live Yahoo quote re-pricing / drift abort
  - order-book slippage; gaps through stops fill at the stop in this model
  - One historical path is evidence, not proof — do not retune on the
    same sample you evaluate.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import logging
import os
from datetime import datetime, timezone

import config
import strategy
from config import ASSET_LIST, effective_decimals
from risk import TP1_R, TP2_R, TP3_R, calculate_trade

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backtest")

SL_BUFFER_PCT = 0.0003     # identical to trade_monitor.py
WARMUP_5M     = 220        # get_signal() needs >= 200 closed 5m bars
WINDOW        = 320        # bars of history handed to the strategy
SCAN_EVERY_5M = 3          # one scan per 15 minutes, matching the live cycle
MAX_HOLD_MIN  = 12 * 60    # abandon a trade that never resolves

# Scale-out weights — same model analytics.py reports with
W1, W2, W3 = 0.50, 0.25, 0.25


# ── session filter driven by bar time, not wall clock ────────────────────

def _session_at(ts_ms: int) -> tuple[str, bool]:
    """
    strategy.get_signal() calls get_current_session(), which reads the real
    clock. In a backtest that would stamp every historical bar with today's
    session. We patch it to derive the session from the bar instead.
    """
    hour = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).hour
    london, new_york, asian = 8 <= hour < 16, 13 <= hour < 21, 0 <= hour < 8
    if london and new_york:
        return "London + New York Overlap", True
    if london:
        return "London", True
    if new_york:
        return "New York", True
    if asian:
        return "Asian", False
    return "Off-Hours", False


# ── resampling ────────────────────────────────────────────────────────────

def resample(bars_1m: list[dict], minutes: int) -> list[dict]:
    """1m bars -> `minutes` bars. Only fully closed buckets are emitted."""
    out: list[dict] = []
    bucket_ms = minutes * 60_000
    cur = None
    for b in bars_1m:
        start = b["ts"] - (b["ts"] % bucket_ms)
        if cur is None or cur["ts"] != start:
            if cur is not None:
                out.append(cur)
            cur = {"ts": start, "open": b["open"], "high": b["high"],
                   "low": b["low"], "close": b["close"], "volume": b["volume"]}
        else:
            cur["high"]   = max(cur["high"], b["high"])
            cur["low"]    = min(cur["low"], b["low"])
            cur["close"]  = b["close"]
            cur["volume"] += b["volume"]
    if cur is not None:
        out.append(cur)
    return out[:-1] if out else out


# ── trade simulation on 1m bars ───────────────────────────────────────────

def simulate_trade(trade: dict, bars_1m: list[dict], start_idx: int) -> dict:
    """
    Walk forward from the first 1m bar AFTER entry and resolve the trade
    exactly the way trade_monitor.py does.
    """
    is_buy = trade["signal"] == "BUY"
    sl, entry = trade["sl"], trade["entry"]
    hit_tp1 = hit_tp2 = False

    deadline = start_idx + MAX_HOLD_MIN

    for i in range(start_idx, min(len(bars_1m), deadline)):
        b = bars_1m[i]
        adverse    = b["low"]  if is_buy else b["high"]
        favourable = b["high"] if is_buy else b["low"]

        buffer = abs(sl) * SL_BUFFER_PCT
        sl_hit = (is_buy and adverse <= sl - buffer) or (not is_buy and adverse >= sl + buffer)

        def reached(level: float) -> bool:
            return (is_buy and favourable >= level) or (not is_buy and favourable <= level)

        if sl_hit:
            # Same pessimistic same-bar convention as the live monitor.
            trade["status"]    = "BE" if hit_tp1 else "SL"
            trade["hit_tp1"]   = hit_tp1
            trade["hit_tp2"]   = hit_tp2
            trade["hit_tp3"]   = False
            trade["closed_ts"] = b["ts"]
            trade["bars_held"] = i - start_idx
            return trade

        if not hit_tp1 and reached(trade["tp1"]):
            hit_tp1 = True
            sl = entry                      # move to breakeven
        if hit_tp1 and not hit_tp2 and reached(trade["tp2"]):
            hit_tp2 = True
        if hit_tp2 and reached(trade["tp3"]):
            trade["status"]    = "TP"
            trade["hit_tp1"]   = trade["hit_tp2"] = trade["hit_tp3"] = True
            trade["closed_ts"] = b["ts"]
            trade["bars_held"] = i - start_idx
            return trade

    trade["status"]    = "TIMEOUT"
    trade["hit_tp1"]   = hit_tp1
    trade["hit_tp2"]   = hit_tp2
    trade["hit_tp3"]   = False
    trade["closed_ts"] = bars_1m[min(len(bars_1m), deadline) - 1]["ts"]
    trade["bars_held"] = min(len(bars_1m), deadline) - start_idx
    return trade


def trade_r(trade: dict, fee_r: float) -> float:
    """R-multiple net of costs, using the same 50/25/25 model as analytics.py."""
    if trade["status"] == "TP":
        gross = W1 * TP1_R + W2 * TP2_R + W3 * TP3_R
    elif trade["status"] == "BE":
        if trade["hit_tp2"]:
            gross = W1 * TP1_R + W2 * TP2_R
        elif trade["hit_tp1"]:
            gross = W1 * TP1_R
        else:
            gross = 0.0
    elif trade["status"] == "SL":
        if trade["hit_tp2"]:
            gross = W1 * TP1_R + W2 * TP2_R - W3
        elif trade["hit_tp1"]:
            gross = W1 * TP1_R - (W2 + W3)
        else:
            gross = -1.0
    else:  # TIMEOUT — close at whatever was banked, remainder flat
        gross = (W1 * TP1_R if trade["hit_tp1"] else 0.0) + \
                (W2 * TP2_R if trade["hit_tp2"] else 0.0)
    return gross - fee_r


# ── the run ───────────────────────────────────────────────────────────────

def run_asset(asset: str, bars_1m: list[dict], fee_bps: float) -> list[dict]:
    # run_asset monkey-patches strategy.get_current_session so the signal
    # engine derives the session from the bar being replayed, not the wall
    # clock. try/finally guarantees the real function is restored even if
    # get_signal or simulate_trade raises mid-loop — otherwise a single bad
    # bar would leave the whole process running on the patched session.
    _real_session = strategy.get_current_session
    try:
        return _run_asset_body(asset, bars_1m, fee_bps)
    finally:
        strategy.get_current_session = _real_session


def _run_asset_body(asset: str, bars_1m: list[dict], fee_bps: float) -> list[dict]:
    bars_5m  = resample(bars_1m, 5)
    bars_15m = resample(bars_1m, 15)

    if len(bars_5m) < WARMUP_5M + 10:
        logger.warning(f"  {asset.upper()}: not enough history ({len(bars_5m)} 5m bars)")
        return []

    # Index 1m bars by timestamp. bisect rather than an exact-match dict:
    # a 5m bucket boundary does not have to coincide with a 1m bar (gaps,
    # unaligned feeds, delisted minutes). An exact-match lookup silently
    # returned 0 for every scan in that case and the whole backtest quietly
    # produced no trades at all, which is the worst possible failure mode.
    ts_1m = [b["ts"] for b in bars_1m]

    def idx_at_or_before(ts: int) -> int:
        return bisect.bisect_right(ts_1m, ts) - 1

    def idx_after(ts: int) -> int | None:
        i = bisect.bisect_right(ts_1m, ts)
        return i if i < len(ts_1m) else None

    ts_15m    = [b["ts"] for b in bars_15m]
    closes_1m = [b["close"] for b in bars_1m]
    trades: list[dict] = []
    cooldown_until_ts = 0
    open_until_ts     = 0     # one trade per asset at a time, like the live bot

    for i in range(WARMUP_5M, len(bars_5m), SCAN_EVERY_5M):
        bar = bars_5m[i]
        now_ts = bar["ts"] + 5 * 60_000        # the bar has just closed

        if now_ts < cooldown_until_ts or now_ts < open_until_ts:
            continue

        window = bars_5m[max(0, i - WINDOW):i + 1]
        close  = [b["close"] for b in window]
        high   = [b["high"] for b in window]
        low    = [b["low"] for b in window]
        openp  = [b["open"] for b in window]
        vol    = [b["volume"] for b in window]

        cut15 = bisect.bisect_right(ts_15m, bar["ts"])
        n15   = bars_15m[max(0, cut15 - WINDOW):cut15]

        end1m = idx_at_or_before(bar["ts"] + 5 * 60_000 - 1)
        n1    = closes_1m[max(0, end1m - WINDOW + 1):end1m + 1]

        if len(n15) < 200 or len(n1) < 200:
            continue

        strategy.get_current_session = lambda ts=now_ts: _session_at(ts)

        price    = close[-1]
        decimals = effective_decimals(asset, price)

        result = strategy.get_signal(
            close, high, low,
            {"1m": n1, "5m": close, "15m": [b["close"] for b in n15]},
            vol, openp, decimals=decimals,
        )
        if result["signal"] not in ("BUY", "SELL"):
            continue

        levels = calculate_trade(
            result["signal"], price, result.get("atr_value", 0),
            decimals=decimals, session_active=result.get("session_active", True),
        )
        risk_dist = levels["risk_distance"]
        if not risk_dist or risk_dist <= 0:
            continue

        # First 1m bar strictly AFTER the 5m bar the signal fired on —
        # the same no-look-ahead rule the live monitor enforces.
        start_idx = idx_after(bar["ts"] + 5 * 60_000 - 1)
        if start_idx is None:
            continue

        # Round-trip cost expressed in R — this is the number that decides
        # whether a 1.2R first target is worth taking at all.
        fee_r = (fee_bps / 10_000.0) * price / risk_dist

        trade = simulate_trade({
            "asset": asset, "signal": result["signal"],
            "entry": levels["entry"], "sl": levels["sl"],
            "tp1": levels["tp1"], "tp2": levels["tp2"], "tp3": levels["tp3"],
            "opened_ts": now_ts, "risk_dist": risk_dist, "price": price,
            "risk_pct": risk_dist / price * 100, "fee_r": fee_r,
        }, bars_1m, start_idx)

        trade["r"] = round(trade_r(trade, fee_r), 4)
        trades.append(trade)

        cooldown_until_ts = now_ts + config.SIGNAL_COOLDOWN_SEC * 1000
        open_until_ts     = trade["closed_ts"]

    return trades


def report(all_trades: list[dict], fee_bps: float, days: int) -> str:
    if not all_trades:
        return "No trades generated. Try more days, or check the data download."

    rs = [t["r"] for t in all_trades]
    wins   = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gross_w, gross_l = sum(wins), abs(sum(losses))

    equity = peak = max_dd = 0.0
    for r in rs:
        equity += r
        peak    = max(peak, equity)
        max_dd  = max(max_dd, peak - equity)

    counts = {}
    for t in all_trades:
        counts[t["status"]] = counts.get(t["status"], 0) + 1

    avg_risk_pct = sum(t["risk_pct"] for t in all_trades) / len(all_trades)
    avg_fee_r    = sum(t["fee_r"] for t in all_trades) / len(all_trades)

    lines = [
        "=" * 58,
        f"BACKTEST — {days} days, {len(all_trades)} trades, fees {fee_bps:.0f} bps round trip",
        "=" * 58,
        "",
        f"Win rate       : {len(wins) / len(rs) * 100:.2f}%   ({len(wins)}W / {len(losses)}L)",
        f"Total          : {equity:+.2f} R",
        f"Expectancy     : {equity / len(rs):+.4f} R per trade",
        f"Avg win        : {gross_w / len(wins):+.2f} R" if wins else "Avg win        : n/a",
        f"Avg loss       : {-gross_l / len(losses):+.2f} R" if losses else "Avg loss       : n/a",
        f"Profit factor  : {gross_w / gross_l:.2f}" if gross_l else "Profit factor  : inf",
        f"Max drawdown   : -{max_dd:.2f} R",
        "",
        "Outcomes       : " + "  ".join(f"{k} {v}" for k, v in sorted(counts.items())),
        "",
        f"Avg stop size  : {avg_risk_pct:.3f}% of price",
        f"Avg cost       : {avg_fee_r:.3f} R per trade  <-- fees as a fraction of your risk",
        "",
        "Per asset:",
    ]

    by_asset: dict[str, list[float]] = {}
    for t in all_trades:
        by_asset.setdefault(t["asset"], []).append(t["r"])
    for a in sorted(by_asset, key=lambda x: -sum(by_asset[x])):
        v = by_asset[a]
        w = sum(1 for r in v if r > 0)
        lines.append(
            f"  {a.upper():<5} {len(v):>3} trades | {w / len(v) * 100:5.1f}% win | "
            f"{sum(v):+7.2f} R | {sum(v) / len(v):+.3f} R/trade"
        )

    lines += [
        "",
        "-" * 58,
        "Expectancy is the only line that matters. Positive means the edge",
        "survived costs on this sample; negative means it did not. One",
        "historical path is evidence, not proof.",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--assets", type=str, default=",".join(ASSET_LIST))
    ap.add_argument("--fee-bps", type=float, default=20.0,
                    help="round-trip cost in basis points (Binance taker ~20)")
    ap.add_argument("--out", type=str, default="backtest_trades.csv")
    args = ap.parse_args()

    from backtest_data import check_connectivity, fetch_asset

    # Fail fast and loudly: Binance returns HTTP 451 to US IPs, which is what
    # Google Colab and Kaggle run on. Without this check that shows up as a
    # ten-minute download that quietly yields nothing.
    host = check_connectivity()
    if not host:
        logger.error(
            "No data source reachable from this machine.\n"
            "Binance blocks US IPs (451) and Coinbase did not answer either.\n"
            "Try running locally instead of on a cloud notebook."
        )
        return
    logger.info(f"Data source: {host}\n")

    assets = [a.strip().lower() for a in args.assets.split(",") if a.strip()]
    all_trades: list[dict] = []

    for asset in assets:
        logger.info(f"[{asset.upper()}] downloading {args.days}d of 1m data…")
        try:
            bars = fetch_asset(asset, args.days)
        except Exception as e:
            logger.error(f"  {asset.upper()}: download failed — {e}")
            continue
        logger.info(f"[{asset.upper()}] {len(bars)} bars — running…")
        t = run_asset(asset, bars, args.fee_bps)
        logger.info(f"[{asset.upper()}] {len(t)} trades")
        all_trades += t

    print()
    print(report(all_trades, args.fee_bps, args.days))

    if all_trades:
        keys = ["asset", "signal", "opened_ts", "closed_ts", "entry", "sl",
                "tp1", "tp2", "tp3", "status", "hit_tp1", "hit_tp2", "hit_tp3",
                "risk_pct", "fee_r", "bars_held", "r"]
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(all_trades)
        print(f"\nTrade-by-trade CSV written to {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
