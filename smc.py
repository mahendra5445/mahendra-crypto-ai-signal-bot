"""
smc.py
Smart-Money-Concept detectors (items 3-11, 39-43).
These are rule-based heuristics (swing-point based). SMC has no single
universally-agreed formal definition, so thresholds are configurable.
"""
import numpy as np
import pandas as pd


def swing_points(df: pd.DataFrame, left: int = 3, right: int = 3):
    """Return boolean masks for swing highs / swing lows."""
    high, low = df["high"], df["low"]
    n = len(df)
    is_high = np.zeros(n, dtype=bool)
    is_low = np.zeros(n, dtype=bool)
    for i in range(left, n - right):
        window_h = high.iloc[i - left : i + right + 1]
        window_l = low.iloc[i - left : i + right + 1]
        if high.iloc[i] == window_h.max():
            is_high[i] = True
        if low.iloc[i] == window_l.min():
            is_low[i] = True
    return pd.Series(is_high, index=df.index), pd.Series(is_low, index=df.index)


def market_structure(df: pd.DataFrame, left=3, right=3):
    """
    Item 3: Market Structure Detection.
    Labels each confirmed swing as HH/HL/LH/LL and flags BOS / CHOCH.
    Returns a DataFrame aligned to df.index with columns:
    swing_type ('HH','HL','LH','LL', or None), structure ('bull'/'bear'/None),
    bos (bool), choch (bool)
    """
    is_high, is_low = swing_points(df, left, right)
    swing_type = pd.Series(index=df.index, dtype=object)
    structure = pd.Series(index=df.index, dtype=object)
    bos = pd.Series(False, index=df.index)
    choch = pd.Series(False, index=df.index)

    last_high, last_low = None, None
    trend = None  # 'bull' or 'bear'

    for i in range(len(df)):
        if is_high.iloc[i]:
            price = df["high"].iloc[i]
            if last_high is not None:
                label = "HH" if price > last_high else "LH"
                swing_type.iloc[i] = label
                if label == "HH" and trend in (None, "bull"):
                    trend = "bull"
                elif label == "LH" and trend == "bull":
                    choch.iloc[i] = True
                    trend = "bear"
                elif label == "HH" and trend == "bear":
                    bos.iloc[i] = True
                    trend = "bull"
            last_high = price
        if is_low.iloc[i]:
            price = df["low"].iloc[i]
            if last_low is not None:
                label = "LL" if price < last_low else "HL"
                swing_type.iloc[i] = label
                if label == "LL" and trend in (None, "bear"):
                    trend = "bear"
                elif label == "HL" and trend == "bear":
                    choch.iloc[i] = True
                    trend = "bull"
                elif label == "LL" and trend == "bull":
                    bos.iloc[i] = True
                    trend = "bear"
            last_low = price
        structure.iloc[i] = trend

    return pd.DataFrame(
        {"swing_type": swing_type, "structure": structure, "bos": bos, "choch": choch}
    )


def order_blocks(df: pd.DataFrame, lookahead: int = 10, impulse_atr_mult: float = 1.5, atr_series=None):
    """
    Item 4: Order Block Detection.
    A bullish OB = last down-candle before a strong up-impulse that breaks
    prior structure; bearish OB = last up-candle before a strong down-impulse.
    Returns list of dicts: {index, type, top, bottom}
    """
    from .indicators import atr as _atr

    a = atr_series if atr_series is not None else _atr(df)
    obs = []
    for i in range(1, len(df) - lookahead):
        candle = df.iloc[i]
        is_down = candle["close"] < candle["open"]
        is_up = candle["close"] > candle["open"]
        future = df.iloc[i + 1 : i + 1 + lookahead]
        move_up = future["close"].max() - candle["close"]
        move_down = candle["close"] - future["close"].min()
        thresh = a.iloc[i] * impulse_atr_mult if not np.isnan(a.iloc[i]) else 0

        if is_down and move_up > thresh:
            obs.append({"index": i, "type": "bullish", "top": candle["open"], "bottom": candle["low"]})
        elif is_up and move_down > thresh:
            obs.append({"index": i, "type": "bearish", "top": candle["high"], "bottom": candle["open"]})
    return obs


def fair_value_gaps(df: pd.DataFrame):
    """
    Item 5: FVG Detection - 3-candle imbalance (candle1.high < candle3.low = bullish gap;
    candle1.low > candle3.high = bearish gap).
    """
    fvgs = []
    high, low = df["high"].values, df["low"].values
    for i in range(2, len(df)):
        if high[i - 2] < low[i]:
            fvgs.append({"index": i, "type": "bullish", "top": low[i], "bottom": high[i - 2]})
        elif low[i - 2] > high[i]:
            fvgs.append({"index": i, "type": "bearish", "top": low[i - 2], "bottom": high[i]})
    return fvgs


def premium_discount_zone(df: pd.DataFrame, lookback: int = 100):
    """
    Item 6: Premium & Discount Zone Filter.
    Splits the rolling swing range into premium (>=70%), equilibrium (30-70%),
    discount (<=30%) zones. Returns the zone label per bar.
    """
    roll_high = df["high"].rolling(lookback).max()
    roll_low = df["low"].rolling(lookback).min()
    rng = (roll_high - roll_low).replace(0, np.nan)
    pos = (df["close"] - roll_low) / rng
    zone = pd.Series(np.where(pos >= 0.7, "premium", np.where(pos <= 0.3, "discount", "equilibrium")),
                      index=df.index)
    return zone, pos


def liquidity_pools(df: pd.DataFrame, left=3, right=3, cluster_tol=0.0015):
    """
    Item 7: Liquidity Pool Detection - clusters of nearby swing highs/lows
    where resting stop-liquidity is presumed to sit.
    """
    is_high, is_low = swing_points(df, left, right)
    highs = df.loc[is_high, "high"]
    lows = df.loc[is_low, "low"]

    def cluster(levels):
        levels = sorted(levels.items())
        pools = []
        for idx, price in levels:
            placed = False
            for pool in pools:
                if abs(price - pool["price"]) / pool["price"] <= cluster_tol:
                    pool["touches"] += 1
                    pool["price"] = (pool["price"] * (pool["touches"] - 1) + price) / pool["touches"]
                    placed = True
                    break
            if not placed:
                pools.append({"price": price, "touches": 1, "first_index": idx})
        return [p for p in pools if p["touches"] >= 2]

    return {"buy_side": cluster(highs), "sell_side": cluster(lows)}


def equal_highs_lows(df: pd.DataFrame, left=3, right=3, tol=0.0008):
    """Item 8: Equal High / Equal Low detection (subset of liquidity pools, tighter tolerance)."""
    pools = liquidity_pools(df, left, right, cluster_tol=tol)
    eqh = [p for p in pools["buy_side"] if p["touches"] >= 2]
    eql = [p for p in pools["sell_side"] if p["touches"] >= 2]
    return {"equal_highs": eqh, "equal_lows": eql}


def inducement(df: pd.DataFrame, structure_df: pd.DataFrame, left=3, right=3):
    """
    Item 9: Inducement - a minor swing point taken out just before price
    reverses back into the direction implied by the higher-level structure
    (a "trap" swing). Approximated as a swing point opposite trend that gets
    swept within 5 bars then price reverses in trend direction.
    """
    is_high, is_low = swing_points(df, left, right)
    inducements = []
    trend = structure_df["structure"]
    for i in range(len(df)):
        cur_trend = trend.iloc[i]
        if cur_trend == "bull" and is_low.iloc[i]:
            level = df["low"].iloc[i]
            window = df.iloc[i + 1 : i + 6]
            if len(window) and window["low"].min() < level and window["close"].iloc[-1] > level:
                inducements.append({"index": i, "type": "bullish_inducement", "level": level})
        elif cur_trend == "bear" and is_high.iloc[i]:
            level = df["high"].iloc[i]
            window = df.iloc[i + 1 : i + 6]
            if len(window) and window["high"].max() > level and window["close"].iloc[-1] < level:
                inducements.append({"index": i, "type": "bearish_inducement", "level": level})
    return inducements


def breaker_blocks(df: pd.DataFrame, structure_df: pd.DataFrame, ob_list: list):
    """
    Item 10: Breaker Block - an order block that fails (price closes back
    through it) and later acts as support/resistance in the opposite direction.
    """
    breakers = []
    for ob in ob_list:
        i = ob["index"]
        future = df.iloc[i + 1 :]
        if ob["type"] == "bullish":
            broken = future[future["close"] < ob["bottom"]]
            if len(broken):
                breakers.append({**ob, "type": "bearish_breaker", "break_index": broken.index[0]})
        else:
            broken = future[future["close"] > ob["top"]]
            if len(broken):
                breakers.append({**ob, "type": "bullish_breaker", "break_index": broken.index[0]})
    return breakers


def mitigation_blocks(df: pd.DataFrame, ob_list: list, tolerance_bars: int = 200):
    """
    Item 11: Mitigation Block - price returns to an order block's origin
    candle range once, without breaking it, "mitigating" the imbalance.
    """
    mitigations = []
    for ob in ob_list:
        i = ob["index"]
        future = df.iloc[i + 1 : i + 1 + tolerance_bars]
        if ob["type"] == "bullish":
            touch = future[(future["low"] <= ob["top"]) & (future["low"] >= ob["bottom"])]
        else:
            touch = future[(future["high"] >= ob["bottom"]) & (future["high"] <= ob["top"])]
        if len(touch):
            mitigations.append({**ob, "mitigated_at": touch.index[0]})
    return mitigations


# ------------------- Smart money confirmation (39-43) ------------------- #
def liquidity_sweep(df: pd.DataFrame, pools: dict, reversal_bars: int = 3):
    """Item 39/40: Liquidity Sweep / Stop Hunt - wick pierces a pool then closes back inside."""
    sweeps = []
    for pool in pools["buy_side"]:
        level = pool["price"]
        for i in range(pool["first_index"] + 1, len(df)):
            if df["high"].iloc[i] > level and df["close"].iloc[i] < level:
                sweeps.append({"index": i, "type": "sell_side_sweep", "level": level})
                break
    for pool in pools["sell_side"]:
        level = pool["price"]
        for i in range(pool["first_index"] + 1, len(df)):
            if df["low"].iloc[i] < level and df["close"].iloc[i] > level:
                sweeps.append({"index": i, "type": "buy_side_sweep", "level": level})
                break
    return sweeps


def fake_breakout(df: pd.DataFrame, lookback: int = 20, wick_thresh: float = 0.5):
    """Item 41: Fake Breakout - closes back inside prior range after piercing it, with a large wick."""
    roll_high = df["high"].rolling(lookback).max().shift(1)
    roll_low = df["low"].rolling(lookback).min().shift(1)
    from .indicators import wick_ratio
    wr = wick_ratio(df)
    fake_up = (df["high"] > roll_high) & (df["close"] < roll_high) & (wr > wick_thresh)
    fake_down = (df["low"] < roll_low) & (df["close"] > roll_low) & (wr > wick_thresh)
    return fake_up, fake_down


def smart_money_entry(df: pd.DataFrame, sweeps: list, fvgs: list, max_gap_bars: int = 5):
    """
    Item 42: Smart Money Entry Confirmation - a liquidity sweep followed
    shortly by a FVG in the opposite (reversal) direction = high-quality entry.
    """
    entries = []
    fvg_by_index = {}
    for f in fvgs:
        fvg_by_index.setdefault(f["type"], []).append(f["index"])
    for s in sweeps:
        want_type = "bullish" if s["type"] == "buy_side_sweep" else "bearish"
        for fi in fvg_by_index.get(want_type, []):
            if 0 <= fi - s["index"] <= max_gap_bars:
                entries.append({"sweep_index": s["index"], "fvg_index": fi, "direction": want_type})
                break
    return entries


def institutional_zone(df: pd.DataFrame, vol_profile: dict, tol_pct: float = 0.002):
    """Item 43: Institutional Zone - price trading near the volume-profile POC/HVN (high acceptance)."""
    poc = vol_profile["poc"]
    near_poc = (df["close"] - poc).abs() / poc <= tol_pct
    return near_poc
