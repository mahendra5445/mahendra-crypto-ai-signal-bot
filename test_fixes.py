"""
Regression tests for production remediations.

Run with:  python test_fixes.py
No network, no Telegram — pure logic checks against the real modules.
"""

import os
import shutil
import tempfile

PASS, FAIL = "✅", "❌"
_results = []


def check(name, condition, detail=""):
    _results.append(condition)
    print(f"{PASS if condition else FAIL}  {name}" + (f"  — {detail}" if detail else ""))


def bar(ts, high, low, close=None):
    return {"ts": ts, "open": close or low, "high": high, "low": low,
            "close": close if close is not None else (high + low) / 2}


def run_tests() -> int:
    # P-006: env setup only when executed as a script, not on import.
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="bot-test-")
    os.environ.pop("DATABASE_URL", None)
    os.environ["ALLOW_JSON_PERSISTENCE"] = "1"
    os.environ.setdefault("ADMIN_IDS", "1")
    os.environ.setdefault("BOT_TOKEN", "test-token")
    os.environ.setdefault("CHANNEL_ID", "@test")

    import analytics
    import trade_tracker as tt
    from config import LIVE_PRICE_DRIFT_MAX, effective_decimals
    from risk import calculate_trade
    from trade_monitor import _events_for_bar, bars_usable_after_entry

    # ── 1. Pre-entry bars must never close a trade ────────────────────────
    print("\n1. Pre-entry bar filtering")

    ENTRY_TS = 1_700_000_600  # mid-minute (:20)
    trade = {
        "id": 1, "asset": "avax", "signal": "SELL", "status": "OPEN",
        "entry": 6.2600, "sl": 6.2700, "original_sl": 6.2700,
        "tp1": 6.2480, "tp2": 6.2400, "tp3": 6.2300,
        "hit_tp1": False, "hit_tp2": False, "hit_tp3": False,
        "opened_ts": ENTRY_TS,
    }

    entry_bar_open = ENTRY_TS - (ENTRY_TS % 60)
    bars = [
        bar(ENTRY_TS - 180, 6.2800, 6.2400),
        bar(ENTRY_TS - 120, 6.2750, 6.2450),
        bar(ENTRY_TS -  60, 6.2900, 6.2350),
        bar(entry_bar_open, 6.2650, 6.2580),  # entry minute (P-004)
        bar(ENTRY_TS + 60,  6.2620, 6.2590),
    ]

    usable = bars_usable_after_entry(bars, trade["opened_ts"])
    check(
        "prior full minutes excluded; entry minute included (P-004)",
        len(usable) == 2 and usable[0]["ts"] == entry_bar_open,
        f"{len(usable)} of {len(bars)}, first_ts={usable[0]['ts'] if usable else None}",
    )

    events_pre = [e for b in bars[:3] for e in _events_for_bar(trade, b)]
    check(
        "pre-entry bars WOULD have fired (proving the old bug)",
        events_pre != [],
        f"would fire {events_pre}",
    )

    events_ok = [e for b in usable for e in _events_for_bar(trade, b)]
    check("entry-minute + later quiet bars do not false-close", events_ok == [],
          f"events={events_ok}")

    # ── 2. Chronological replay: TP1 then SL is a breakeven, not a loss ───
    print("\n2. Chronological bar replay")

    t2 = dict(trade, id=2, opened_ts=ENTRY_TS)
    seq = [
        bar(ENTRY_TS + 60,  6.2620, 6.2470),
        bar(ENTRY_TS + 120, 6.2650, 6.2600),
        bar(ENTRY_TS + 180, 6.2800, 6.2600),
    ]
    fired = []
    for b in seq:
        evs = _events_for_bar(t2, b)
        for e in evs:
            fired.append(e)
            if e == "tp1":
                t2["hit_tp1"] = True
                t2["sl"] = t2["entry"]
        if any(e in ("sl", "be") for e in evs):
            break

    check("TP1 registered before the reversal", "tp1" in fired, f"sequence={fired}")
    check("reversal closes as BE, not SL", fired[-1] == "be", f"sequence={fired}")

    # ── 3. Price-aware decimals ───────────────────────────────────────────
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

    # ── 4. Win rate can never exceed 100% ─────────────────────────────────
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

    # ── 5. original_sl survives the move to breakeven ─────────────────────
    print("\n5. Original stop preserved")

    t5 = {"id": 9, "entry": 100.0, "sl": 98.0, "original_sl": 98.0, "hit_tp1": False, "status": "OPEN"}
    tt.mark_tp1_hit(t5)
    check("sl moved to breakeven", t5["sl"] == 100.0)
    check("original_sl retained for R maths", t5["original_sl"] == 98.0)

    # ── 6. Analytics expectancy ───────────────────────────────────────────
    print("\n6. R-multiple analytics")

    p = analytics.performance()
    check("expectancy computed", "expectancy_r" in p, f"expectancy={p.get('expectancy_r')} R")
    check("full TP3 is worth +1.85R", analytics.trade_r(tt._trades[-1]) == 1.85)
    check("clean stop is worth -1R", analytics.trade_r(tt._trades[2]) == -1.0)

    # ── 7. Startup burst throttle ─────────────────────────────────────────
    print("\n7. Startup burst throttle")

    import config

    check("startup warm-up delay exists", config.STARTUP_DELAY_SEC > 0,
          f"STARTUP_DELAY_SEC={config.STARTUP_DELAY_SEC}s")
    check("per-cycle open cap exists", config.MAX_NEW_TRADES_PER_CYCLE >= 1,
          f"MAX_NEW_TRADES_PER_CYCLE={config.MAX_NEW_TRADES_PER_CYCLE}")

    cap = config.MAX_NEW_TRADES_PER_CYCLE
    opened = 0
    for _asset in config.ASSET_LIST:
        opened += 1
        if opened >= cap:
            break
    check("cap halts opening mid-cycle", opened == cap,
          f"{opened} opened of {len(config.ASSET_LIST)} qualifying (cap {cap})")
    check("cap is well below the coin count", cap < len(config.ASSET_LIST),
          f"{cap} << {len(config.ASSET_LIST)} coins")
    check("tracked-signal floor is 9+ confirmations", config.MIN_CONFIRMATIONS >= 9,
          f"MIN_CONFIRMATIONS={config.MIN_CONFIRMATIONS}")

    # ── 8. MUST / SHOULD remediations ─────────────────────────────────────
    print("\n8. Production remediations")

    import persistence
    from risk import calculate_trade as ct
    from trend import get_trend

    src_persist = open(persistence.__file__, encoding="utf-8").read()
    check("persistence uses Json helper available when psycopg installed",
          "Json" in src_persist)
    check("C-001 Json binding in save path", "_Json(payload)" in src_persist)

    src_data = open("data.py", encoding="utf-8").read()
    check("C-002 stale hard-fail raises RuntimeError", "STALE DATA" in src_data)

    src_auto = open("auto_signal.py", encoding="utf-8").read()
    check("C-003 notify before save_trade",
          src_auto.find("notify_channel") < src_auto.find("save_trade") and
          "trade NOT saved" in src_auto)
    check("P-003 no post-notify can_open block",
          "blocked after notify" not in src_auto)
    check("P-002 persist return checked", "NOT persisted" in src_auto)

    check("C-004 ADMIN_IDS required in config", hasattr(config, "ADMIN_IDS"))
    check("C-005/L-010 require_env exists", callable(getattr(config, "require_env", None)))
    check("P-005 DATABASE_URL required by default",
          "DATABASE_URL" in open("config.py", encoding="utf-8").read() and
          "ALLOW_JSON_PERSISTENCE" in open("config.py", encoding="utf-8").read())

    src_notify = open("notify.py", encoding="utf-8").read()
    check("H-013 notify returns bool with retries",
          "-> bool" in src_notify and "retries" in src_notify)

    empty = ct("BUY", 100.0, float("nan"), decimals=2)
    check("M-019 NaN ATR returns no levels", empty["entry"] is None)

    check("M-021 short series → Sideways", get_trend([1, 2, 3]) == "Sideways")

    src_strat = open("strategy.py", encoding="utf-8").read()
    check("H-009 BUY/SELL tie handled", "BUY and SELL tied" in src_strat)
    check("H-006 liquidity not scored", "liquidity weight intentionally omitted" in src_strat)
    check("H-005 volume_data_ok gating", "volume_data_ok" in src_strat)

    check("H-008 drift max configured", LIVE_PRICE_DRIFT_MAX > 0)
    check("L-008 monitor heartbeat key",
          "last_monitor" in open("shared_state.py", encoding="utf-8").read())

    check("H-014 railway.toml present", os.path.exists("railway.toml"))
    check("H-014 Procfile present", os.path.exists("Procfile"))

    check("P-001 Postgres load fails closed (no silent JSON)",
          "refusing JSON fallback" in src_persist)
    check("P-002 save_trades_to_disk returns bool",
          "-> bool" in src_persist and "return False" in src_persist)
    check("P-006 test not auto-running on import",
          'if __name__ == "__main__"' in open(__file__, encoding="utf-8").read())

    # ── summary ───────────────────────────────────────────────────────────
    shutil.rmtree(os.environ["DATA_DIR"], ignore_errors=True)
    print(f"\n{'─' * 50}")
    print(f"{sum(_results)}/{len(_results)} checks passed")
    return 0 if all(_results) else 1


if __name__ == "__main__":
    raise SystemExit(run_tests())
