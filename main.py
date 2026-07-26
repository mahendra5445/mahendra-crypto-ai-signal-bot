"""
main.py — Mahendra Crypto AI Signal Bot (BTC), backtest runner.

Wires together indicators.py, smc.py, sessions_external.py, scoring.py,
risk.py, trade_management.py, journal.py, regime.py, data_layer.py to run
a full backtest on the uploaded BTCUSDT 1m May-2026 file and produce:
  - trade_journal.csv / mahendra.db
  - performance_report.json
  - monthly_report.csv
"""
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mahendra_bot import indicators as ind
from mahendra_bot import smc
from mahendra_bot import sessions_external as sess
from mahendra_bot import scoring as sc
from mahendra_bot import risk as rk
from mahendra_bot import trade_management as tm
from mahendra_bot import journal as jr
from mahendra_bot import regime as rg
from mahendra_bot import data_layer as dl

CSV_PATH = "/home/claude/data/BTCUSDT-1m-2026-05.csv"
OUT_DIR = "/home/claude/mahendra_bot/output"
os.makedirs(OUT_DIR, exist_ok=True)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=df.index)
    a = ind.atr(df)
    f["atr"] = a
    f["atr_z"] = ind.dynamic_atr_filter(df)
    f["ema_slope"] = ind.ema_slope(df["close"], 50, 5)
    f["ema_dist"] = ind.ema_distance(df["close"], 50)
    f["rsi"] = ind.rsi(df["close"])
    macd_line, macd_sig, macd_hist = ind.macd(df["close"])
    f["macd_hist"] = macd_hist
    f["adx"] = ind.adx(df)
    f["vwap_dist"] = ind.vwap_distance(df)
    f["body_ratio"] = ind.strong_body_ratio(df)
    f["wick_ratio"] = ind.wick_ratio(df)
    f["momentum_candle"] = ind.is_momentum_candle(df, atr_series=a)
    f["exhaustion_candle"] = ind.is_exhaustion_candle(df)
    f["vol_spike"] = ind.volume_spike(df)
    f["rvol"] = ind.rvol(df)
    f["buy_sell_ratio"] = ind.buy_sell_ratio(df)
    f["delta_vol"] = ind.delta_volume(df)
    f["low_liquidity"] = ind.low_liquidity_flag(df)
    return f


def build_mtf_trend(df: pd.DataFrame, rule: str) -> pd.Series:
    """Items 21-24: resample to a higher timeframe, compute EMA-slope trend, map back to 1m index."""
    t = pd.to_datetime(df["open_time"], unit="us")
    htf = df.set_index(t).resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    htf_slope = ind.ema_slope(htf["close"], 20, 3)
    htf_trend = np.sign(htf_slope).reindex(t, method="ffill")
    return pd.Series(htf_trend.values, index=df.index).fillna(0)


def build_votes_row(i, df, feats, structure_df, session_df, tf30_trend, tf1h_trend,
                     event_flags) -> dict:
    row = df.iloc[i]
    fr = feats.iloc[i]
    votes = {}
    votes["trend_ema"] = float(np.sign(fr["ema_slope"])) if not np.isnan(fr["ema_slope"]) else 0
    struct = structure_df["structure"].iloc[i]
    votes["structure"] = 1 if struct == "bull" else -1 if struct == "bear" else 0
    votes["order_block"] = event_flags["ob"].iloc[i]
    votes["fvg"] = event_flags["fvg"].iloc[i]
    zone = session_df.get("zone")
    votes["premium_discount"] = event_flags["pd_zone"].iloc[i]
    votes["liquidity_sweep"] = event_flags["sweep"].iloc[i]
    votes["smart_money_entry"] = event_flags["sme"].iloc[i]
    votes["momentum_candle"] = (1 if row["close"] > row["open"] else -1) if fr["momentum_candle"] else 0
    votes["rsi"] = 1 if fr["rsi"] > 55 else -1 if fr["rsi"] < 45 else 0
    votes["macd"] = 1 if fr["macd_hist"] > 0 else -1 if fr["macd_hist"] < 0 else 0
    votes["adx"] = votes["trend_ema"] if fr["adx"] > 22 else 0
    votes["vwap"] = 1 if fr["vwap_dist"] > 0 else -1
    votes["volume_spike"] = (1 if row["close"] > row["open"] else -1) if fr["vol_spike"] else 0
    votes["rvol"] = (1 if row["close"] > row["open"] else -1) if fr["rvol"] > 1.5 else 0
    mtf = tf30_trend.iloc[i] + tf1h_trend.iloc[i]
    votes["mtf_confirmation"] = float(np.sign(mtf))
    votes["session_quality"] = 1 if (session_df["london_kz"].iloc[i] or session_df["ny_kz"].iloc[i]) else 0
    votes["equal_high_low"] = event_flags["eqhl"].iloc[i]
    votes["inducement"] = event_flags["inducement"].iloc[i]
    votes["breaker_block"] = event_flags["breaker"].iloc[i]
    votes["mitigation_block"] = event_flags["mitigation"].iloc[i]
    votes["fake_breakout"] = event_flags["fake_breakout"].iloc[i]
    votes["institutional_zone"] = event_flags["institutional"].iloc[i]
    return votes


def precompute_event_flags(df, ob_list, fvg_list, pd_zone, sweeps, sme_entries,
                            eqhl, inducements, breakers, mitigations, fake_up, fake_down,
                            near_poc, ema_slope, window=15):
    n = len(df)
    ob_flag = np.zeros(n)
    fvg_flag = np.zeros(n)
    sweep_flag = np.zeros(n)
    sme_flag = np.zeros(n)
    eqhl_flag = np.zeros(n)
    inducement_flag = np.zeros(n)
    breaker_flag = np.zeros(n)
    mitigation_flag = np.zeros(n)

    for ob in ob_list:
        i = ob["index"]
        sign = 1 if ob["type"] == "bullish" else -1
        ob_flag[i : min(n, i + window)] = sign
    for f in fvg_list:
        i = f["index"]
        sign = 1 if f["type"] == "bullish" else -1
        fvg_flag[i : min(n, i + window)] = sign
    for s in sweeps:
        i = s["index"]
        sign = 1 if s["type"] == "buy_side_sweep" else -1
        sweep_flag[i : min(n, i + window)] = sign
    for e in sme_entries:
        i = e["fvg_index"]
        sign = 1 if e["direction"] == "bullish" else -1
        sme_flag[i : min(n, i + window)] = sign

    # item 8: equal highs act as resistance (bearish tag), equal lows as support (bullish tag)
    for pool in eqhl.get("equal_highs", []):
        i = pool["first_index"]
        eqhl_flag[i : min(n, i + window)] = -1
    for pool in eqhl.get("equal_lows", []):
        i = pool["first_index"]
        eqhl_flag[i : min(n, i + window)] = 1

    # item 9: inducement sweep-and-reverse - vote in the direction of the reversal
    for ind_ in inducements:
        i = ind_["index"]
        sign = 1 if ind_["type"] == "bullish_inducement" else -1
        inducement_flag[i : min(n, i + window)] = sign

    # item 10: breaker blocks flip polarity vs their origin order block
    for b in breakers:
        i = int(b["break_index"])
        sign = 1 if b["type"] == "bullish_breaker" else -1
        breaker_flag[i : min(n, i + window)] = sign

    # item 11: mitigation - price returning to an unmitigated OB without breaking it
    for m in mitigations:
        i = int(m["mitigated_at"])
        sign = 1 if m["type"] == "bullish" else -1
        mitigation_flag[i : min(n, i + window)] = sign

    pd_sign = np.where(pd_zone.values == "discount", 1, np.where(pd_zone.values == "premium", -1, 0))

    # item 41: fake breakout - trapped breakout traders -> fade the trap
    fake_breakout_sign = np.where(fake_up.values, -1, np.where(fake_down.values, 1, 0))

    # item 43: institutional zone (near volume-profile POC) -> confluence with prevailing trend
    institutional_sign = np.where(near_poc.values, np.sign(ema_slope.fillna(0).values), 0)

    return {
        "ob": pd.Series(ob_flag, index=df.index),
        "fvg": pd.Series(fvg_flag, index=df.index),
        "pd_zone": pd.Series(pd_sign, index=df.index),
        "sweep": pd.Series(sweep_flag, index=df.index),
        "sme": pd.Series(sme_flag, index=df.index),
        "eqhl": pd.Series(eqhl_flag, index=df.index),
        "inducement": pd.Series(inducement_flag, index=df.index),
        "breaker": pd.Series(breaker_flag, index=df.index),
        "mitigation": pd.Series(mitigation_flag, index=df.index),
        "fake_breakout": pd.Series(fake_breakout_sign, index=df.index),
        "institutional": pd.Series(institutional_sign, index=df.index),
    }


def run_backtest(max_bars=None):
    print("Loading + integrity-checking data...")
    df = dl.load_klines_csv(CSV_PATH, max_bars=max_bars)
    print(f"{len(df)} bars loaded.")

    cfg = {
        "account_balance": 1000.0, "risk_per_trade_pct": 1.0, "atr_sl_mult": 1.5,
        "max_open_trades": 1,
    }
    problems = dl.validate_config(cfg)
    if problems:
        raise ValueError(f"Config invalid: {problems}")

    print("Building indicator features...")
    feats = build_features(df)

    print("Detecting market structure / SMC constructs...")
    structure_df = smc.market_structure(df)
    ob_list = smc.order_blocks(df, atr_series=feats["atr"])
    fvg_list = smc.fair_value_gaps(df)
    pd_zone, _ = smc.premium_discount_zone(df)
    pools = smc.liquidity_pools(df)
    sweeps = smc.liquidity_sweep(df, pools)
    sme_entries = smc.smart_money_entry(df, sweeps, fvg_list)
    eqhl = smc.equal_highs_lows(df)
    inducements = smc.inducement(df, structure_df)
    breakers = smc.breaker_blocks(df, structure_df, ob_list)
    mitigations = smc.mitigation_blocks(df, ob_list)
    fake_up, fake_down = smc.fake_breakout(df)
    vol_profile = ind.volume_profile(df)
    near_poc = smc.institutional_zone(df, vol_profile)
    event_flags = precompute_event_flags(
        df, ob_list, fvg_list, pd_zone, sweeps, sme_entries,
        eqhl, inducements, breakers, mitigations, fake_up, fake_down,
        near_poc, feats["ema_slope"],
    )
    print(
        f"SMC construct counts -> OB:{len(ob_list)} FVG:{len(fvg_list)} sweeps:{len(sweeps)} "
        f"SME:{len(sme_entries)} eqH:{len(eqhl['equal_highs'])} eqL:{len(eqhl['equal_lows'])} "
        f"inducements:{len(inducements)} breakers:{len(breakers)} mitigations:{len(mitigations)} "
        f"POC:{vol_profile['poc']:.1f}"
    )

    print("Building multi-timeframe trend + sessions...")
    tf30_trend = build_mtf_trend(df, "30min")
    tf1h_trend = build_mtf_trend(df, "1h")
    session_df = sess.session_filters(df)

    regime = rg.market_regime(df)
    vol_regime = rg.volatility_regime(df)

    weight_store = sc.WeightStore(os.path.join(OUT_DIR, "weights.json"))
    risk_cfg = rk.RiskConfig(
        account_balance=cfg["account_balance"],
        risk_per_trade_pct=cfg["risk_per_trade_pct"],
        max_open_trades=cfg["max_open_trades"],
    )

    risk_mgr = rk.RiskManager(risk_cfg)
    trade_cfg = tm.TradeConfig()
    journal = jr.TradeJournal(os.path.join(OUT_DIR, "mahendra.db"))

    open_trade = None
    ts_series = pd.to_datetime(df["open_time"], unit="us")

    print("Running signal generation + trade simulation loop...")
    for i in range(60, len(df)):
        bar = df.iloc[i]
        ts = ts_series.iloc[i].to_pydatetime()

        if open_trade is not None:
            closed, reason, exit_price, pnl_r = tm.update_trade(
                open_trade, bar, i, feats["atr"].iloc[i], trade_cfg
            )
            if closed:
                pnl_pct = pnl_r * risk_cfg.risk_per_trade_pct
                journal.log_trade(
                    {
                        "signal_key": f"{open_trade.entry_bar}-{open_trade.direction}",
                        "entry_time": ts_series.iloc[open_trade.entry_bar].isoformat(),
                        "exit_time": ts.isoformat(),
                        "direction": open_trade.direction,
                        "entry_price": open_trade.entry,
                        "exit_price": exit_price,
                        "stop_loss": open_trade.stop_loss,
                        "size": open_trade.size,
                        "pnl_r": pnl_r,
                        "pnl_pct": pnl_pct,
                        "exit_reason": reason,
                        "confirmations": open_trade.confirmations,
                        "ai_score": open_trade.ai_score,
                    }
                )
                risk_mgr.register_trade_close(pnl_pct)
                open_trade = None
            continue  # only manage one trade at a time (max_open_trades=1)

        votes = build_votes_row(i, df, feats, structure_df, session_df, tf30_trend, tf1h_trend, event_flags)
        result = sc.compute_ai_score(votes, weight_store.weights)
        if result["direction"] == "neutral":
            continue
        if not sc.minimum_confirmation_score(result, min_score=68):
            continue
        if feats["low_liquidity"].iloc[i]:
            continue

        entry = bar["close"]
        a = feats["atr"].iloc[i]
        if np.isnan(a) or a <= 0:
            continue

        # item 1 wired in: reject signals during abnormal/erratic volatility spikes
        atr_z = feats["atr_z"].iloc[i]
        if not np.isnan(atr_z) and abs(atr_z) > 4:
            continue

        # Bucket price into ~0.5-ATR bands so a genuinely repeated signal at the
        # same level/direction gets blocked (a raw bar-index key is always
        # unique and would never trigger this protection).
        price_bucket = round(entry / (a * 0.5)) if a > 0 else round(entry)
        signal_key = f"{result['direction']}-{price_bucket}"
        can_trade, why = risk_mgr.can_trade(ts, signal_key)
        if not can_trade:
            continue
        direction = "long" if result["direction"] == "long" else "short"
        stop_loss = entry - a * trade_cfg.atr_sl_mult if direction == "long" else entry + a * trade_cfg.atr_sl_mult
        size = risk_mgr.position_size(entry, stop_loss)
        if size <= 0:
            continue

        open_trade = tm.OpenTrade(
            direction=direction, entry=entry, stop_loss=stop_loss,
            initial_risk=abs(entry - stop_loss), size=size, entry_bar=i,
            confirmations=result["confirmations"], ai_score=result["score"],
        )
        risk_mgr.register_trade_open(ts, signal_key)

    trades_df = journal.to_dataframe()
    report = jr.performance_report(trades_df)
    monthly = jr.monthly_report(trades_df)

    weight_store.update_from_trades(trades_df)

    journal.export_csv(os.path.join(OUT_DIR, "trade_journal.csv"))
    monthly.to_csv(os.path.join(OUT_DIR, "monthly_report.csv"))
    with open(os.path.join(OUT_DIR, "performance_report.json"), "w") as f:
        json.dump(report, f, indent=2, default=str)

    print("\n=== PERFORMANCE REPORT ===")
    print(json.dumps(report, indent=2, default=str))
    print(f"\nRegime distribution:\n{regime.value_counts()}")
    print(f"\nVolatility regime distribution:\n{vol_regime.value_counts()}")

    return report, trades_df


if __name__ == "__main__":
    run_backtest()
