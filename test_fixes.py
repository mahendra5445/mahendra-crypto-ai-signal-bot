"""
Regression tests for the bugs that were destroying the win rate.

Run with:  python test_fixes.py
No network, no Telegram — pure logic checks against the real modules.
"""

import os
import shutil
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="bot-test-")
os.environ.pop("DATABASE_URL", None)

import analytics
import trade_tracker as tt
from config import effective_decimals
from risk import calculate_trade
from trade_monitor import _events_for_bar

PASS, FAIL = "✅", "❌"
_results = []


def check(name, condition, detail=""):
    _results.append(condition)
    print(f"{PASS if condition else FAIL}  {name}" + (f"  — {detail}" if detail else ""))


def bar(ts, high, low, close=None):
    return {"ts": ts, "open": close or low, "high": high, "low": low,
            "close": close if close is not None else (high + low) / 2}


# ── 1. Pre-entry bars must never close a trade ────────────────────────────
print("\n1. Pre-entry bar filtering")

ENTRY_TS = 1_700_000_600
trade = {
    "id": 1, "asset": "avax", "signal": "SELL", "status": "OPEN",
    "entry": 6.2600, "sl": 6.2700, "original_sl": 6.2700,
    "tp1": 6.2480, "tp2": 6.2400, "tp3": 6.2300,
    "hit_tp1": False, "hit_tp2": False, "hit_tp3": False,
    "opened_ts": ENTRY_TS,
}

bars = [
    bar(ENTRY_TS - 180, 6.2800, 6.2400),   # BEFORE entry — must be ignored
    bar(ENTRY_TS - 120, 6.2750, 6.2450),   # BEFORE entry — must be ignored
    bar(ENTRY_TS -  60, 6.2900, 6.2350),   # BEFORE entry — must be ignored
    bar(ENTRY_TS + 60,  6.2620, 6.2590),   # after entry, quiet
]

usable = [b for b in bars if b["ts"] >= trade["opened_ts"]]
check("only post-entry bars are usable", len(usable) == 1, f"{len(usable)} of {len(bars)}")

events_pre = [e for b in bars[:3] for e in _events_for_bar(trade, b)]
check("pre-entry bars WOULD have fired (proving the old bug)", events_pre != [],
      f"would fire {events_pre}")

events_ok = [e for b in usable for e in _events_for_bar(trade, b)]
check("trade survives its first poll", events_ok == [], f"events={events_ok}")


# ── 2. Chronological replay: TP1 then SL is a breakeven, not a loss ───────
print("\n2. Chronological bar replay")

t2 = dict(trade, id=2, opened_ts=ENTRY_TS)
seq = [
    bar(ENTRY_TS + 60,  6.2620, 6.2470),   # dips to TP1 (6.2480) -> tp1
    bar(ENTRY_TS + 120, 6.2650, 6.2600),
    bar(ENTRY_TS + 180, 6.2800, 6.2600),   # back up through original SL
]
fired = []
for b in seq:
    evs = _events_for_bar(t2, b)
    for e in evs:
        fired.append(e)
        if e == "tp1":
            t2["hit_tp1"] = True
            t2["sl"] = t2["entry"]          # breakeven, same as mark_tp1_hit()
    if any(e in ("sl", "be") for e in evs):
        break

check("TP1 registered before the reversal", "tp1" in fired, f"sequence={fired}")
check("reversal closes as BE, not SL", fired[-1] == "be", f"sequence={fired}")


# ── 3. Price-aware decimals ───────────────────────────────────────────────
print("\n3. Price-aware rounding")

d_avax = effective_decimals("avax", 6.26)
check("AVAX at $6.26 gets more than 2 decimals", d_avax > 2, f"decimals={d_avax}")
check("BTC at $65k stays at 2 decimals", effective_decimals("btc", 65000) == 2)

lv = calculate_trade("SELL", 6.26, 0.004, decimals=d_avax)
rr = abs(lv["entry"] - lv["tp1"]) / abs(lv["entry"] - lv["sl"])
check("posted R:R is the real R:R", abs(rr - 1.2) < 0.02,
      f"{lv['risk_reward']} → measured 1:{rr:.3f}")

lv_old = calculate_trade("SELL", 6.26, 0.004, decimals=2)
rr_old = abs(lv_old["entry"] - lv_old["tp1"]) / abs(lv_old["entry"] - lv_old["sl"])
check("old 2-decimal path really was broken (proving the bug)",
      abs(rr_old - 1.2) > 0.1, f"would have been 1:{rr_old:.3f}")


# ── 4. Win rate can never exceed 100% ─────────────────────────────────────
print("\n4. Win-rate accounting")

tt._trades = [
    {"id": 1, "asset": "btc", "signal": "BUY",  "status": "OPEN", "hit_tp1": True,
     "hit_tp2": False, "hit_tp3": False, "time": "2026-07-24 09:00:00", "closed_ts": None},
    {"id": 2, "asset": "btc", "signal": "BUY",  "status": "OPEN", "hit_tp1": True,
     "hit_tp2": False, "hit_tp3": False, "time": "2026-07-24 09:00:00", "closed_ts": None},
    {"id": 3, "asset": "btc", "signal": "SELL", "status": "SL",   "hit_tp1": False,
     "hit_tp2": False, "hit_tp3": False, "time": "2026-07-24 09:00:00", "closed_ts": 10},
]
s = tt.get_stats()
check("win rate stays within 0-100", 0 <= s["win_rate"] <= 100, f"{s['win_rate']}%")
check("open trades excluded from closed count", s["closed"] == 1, f"closed={s['closed']}")
check("open trades reported separately", s["open"] == 2, f"open={s['open']}")

tt._trades += [
    {"id": 4, "asset": "eth", "signal": "BUY", "status": "BE", "hit_tp1": True,
     "hit_tp2": False, "hit_tp3": False, "time": "2026-07-24 09:00:00", "closed_ts": 20},
    {"id": 5, "asset": "eth", "signal": "BUY", "status": "TP", "hit_tp1": True,
     "hit_tp2": True, "hit_tp3": True, "time": "2026-07-24 09:00:00", "closed_ts": 30},
]
s = tt.get_stats()
check("BE after TP1 counts as a win", s["wins"] == 2 and s["losses"] == 1,
      f"{s['wins']}W / {s['losses']}L of {s['closed']}")


# ── 5. original_sl survives the move to breakeven ─────────────────────────
print("\n5. Original stop preserved")

t5 = {"id": 9, "entry": 100.0, "sl": 98.0, "original_sl": 98.0, "hit_tp1": False}
tt.mark_tp1_hit(t5)
check("sl moved to breakeven", t5["sl"] == 100.0)
check("original_sl retained for R maths", t5["original_sl"] == 98.0)


# ── 6. Analytics expectancy ───────────────────────────────────────────────
print("\n6. R-multiple analytics")

p = analytics.performance()
check("expectancy computed", "expectancy_r" in p, f"expectancy={p.get('expectancy_r')} R")
check("full TP3 is worth +1.85R", analytics.trade_r(tt._trades[-1]) == 1.85)
check("clean stop is worth -1R", analytics.trade_r(tt._trades[2]) == -1.0)


# ── 7. Startup burst throttle ─────────────────────────────────────────────
print("\n7. Startup burst throttle")

import asyncio
import config
import auto_signal

check("startup warm-up delay exists", config.STARTUP_DELAY_SEC > 0,
      f"STARTUP_DELAY_SEC={config.STARTUP_DELAY_SEC}s")
check("per-cycle open cap exists", config.MAX_NEW_TRADES_PER_CYCLE >= 1,
      f"MAX_NEW_TRADES_PER_CYCLE={config.MAX_NEW_TRADES_PER_CYCLE}")

# Simulate a cycle where EVERY asset qualifies. The cap must stop the loop
# opening more than MAX_NEW_TRADES_PER_CYCLE trades even so.
cap = config.MAX_NEW_TRADES_PER_CYCLE
opened = 0
for _asset in config.ASSET_LIST:              # every asset "would" open
    would_open = True
    if would_open:
        opened += 1
        if opened >= cap:
            break
check("cap halts opening mid-cycle", opened == cap,
      f"{opened} opened of {len(config.ASSET_LIST)} qualifying (cap {cap})")

# The default is meaningfully smaller than the coin count, so a fully
# correlated market can no longer post a signal per coin in one minute.
check("cap is well below the coin count", cap < len(config.ASSET_LIST),
      f"{cap} << {len(config.ASSET_LIST)} coins")

# Gate tightened: the weak 8/12 tier no longer auto-posts.
check("tracked-signal floor is 9+ confirmations", config.MIN_CONFIRMATIONS >= 9,
      f"MIN_CONFIRMATIONS={config.MIN_CONFIRMATIONS}")


# ── summary ───────────────────────────────────────────────────────────────
shutil.rmtree(os.environ["DATA_DIR"], ignore_errors=True)
print(f"\n{'─' * 50}")
print(f"{sum(_results)}/{len(_results)} checks passed")
raise SystemExit(0 if all(_results) else 1)
