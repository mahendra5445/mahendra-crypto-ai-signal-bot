"""
indicators.py
Core technical + volume indicators used across the Mahendra bot.
All functions operate on a pandas DataFrame with columns:
open_time, open, high, low, close, volume, close_time, quote_volume,
trades, taker_buy_base, taker_buy_quote
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- #
# 1. Dynamic ATR (adaptive volatility)
# ---------------------------------------------------------------- #
def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def dynamic_atr_filter(df: pd.DataFrame, period: int = 14, lookback: int = 100) -> pd.Series:
    """Flags bars where current volatility is expanding vs its own recent regime."""
    a = atr(df, period)
    a_mean = a.rolling(lookback).mean()
    a_std = a.rolling(lookback).std()
    z = (a - a_mean) / a_std.replace(0, np.nan)
    return z.fillna(0)


# ---------------------------------------------------------------- #
# 2. EMA slope + EMA distance (trend strength)
# ---------------------------------------------------------------- #
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def ema_slope(series: pd.Series, period: int = 50, lookback: int = 5) -> pd.Series:
    e = ema(series, period)
    return (e - e.shift(lookback)) / lookback


def ema_distance(close: pd.Series, period: int = 50) -> pd.Series:
    e = ema(close, period)
    return (close - e) / e * 100


# ---------------------------------------------------------------- #
# Momentum indicators (26-33)
# ---------------------------------------------------------------- #
def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast=12, slow=26, signal=9):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = atr(df, period)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def vwap(df: pd.DataFrame, reset_daily: bool = True) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = tp * df["volume"]
    if reset_daily:
        day = pd.to_datetime(df["open_time"], unit="us").dt.date
        cum_pv = pv.groupby(day).cumsum()
        cum_v = df["volume"].groupby(day).cumsum()
    else:
        cum_pv, cum_v = pv.cumsum(), df["volume"].cumsum()
    return cum_pv / cum_v.replace(0, np.nan)


def vwap_distance(df: pd.DataFrame) -> pd.Series:
    v = vwap(df)
    return (df["close"] - v) / v * 100


# ---------------------------------------------------------------- #
# Candle / momentum candle filters (26-29)
# ---------------------------------------------------------------- #
def strong_body_ratio(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    body = (df["close"] - df["open"]).abs()
    return body / rng


def wick_ratio(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    upper = df["high"] - df[["open", "close"]].max(axis=1)
    lower = df[["open", "close"]].min(axis=1) - df["low"]
    return (upper + lower) / rng


def is_momentum_candle(df: pd.DataFrame, body_thresh=0.6, atr_mult=1.2, atr_series=None) -> pd.Series:
    a = atr_series if atr_series is not None else atr(df)
    rng = df["high"] - df["low"]
    return (strong_body_ratio(df) > body_thresh) & (rng > a * atr_mult)


def is_exhaustion_candle(df: pd.DataFrame, wick_thresh=0.6) -> pd.Series:
    return wick_ratio(df) > wick_thresh


# ---------------------------------------------------------------- #
# Volume indicators (34-38)
# ---------------------------------------------------------------- #
def volume_spike(df: pd.DataFrame, lookback=20, z_thresh=2.0) -> pd.Series:
    v = df["volume"]
    mean, std = v.rolling(lookback).mean(), v.rolling(lookback).std()
    z = (v - mean) / std.replace(0, np.nan)
    return z.fillna(0) > z_thresh


def rvol(df: pd.DataFrame, lookback=20) -> pd.Series:
    v = df["volume"]
    return v / v.rolling(lookback).mean().replace(0, np.nan)


def buy_sell_ratio(df: pd.DataFrame) -> pd.Series:
    """Approximated from taker_buy_base vs total volume (Binance kline field)."""
    buy = df["taker_buy_base"]
    sell = (df["volume"] - buy).replace(0, np.nan)
    return buy / sell


def delta_volume(df: pd.DataFrame) -> pd.Series:
    return df["taker_buy_base"] - (df["volume"] - df["taker_buy_base"])


def low_liquidity_flag(df: pd.DataFrame, lookback=50, pct=0.3) -> pd.Series:
    v = df["volume"]
    thresh = v.rolling(lookback).mean() * pct
    return v < thresh


# ---------------------------------------------------------------- #
# Volume profile (POC / HVN / LVN) - item 12
# ---------------------------------------------------------------- #
def volume_profile(df: pd.DataFrame, bins: int = 24):
    lo, hi = df["low"].min(), df["high"].max()
    edges = np.linspace(lo, hi, bins + 1)
    typical = (df["high"] + df["low"] + df["close"]) / 3
    idx = np.clip(np.digitize(typical, edges) - 1, 0, bins - 1)
    vol_by_bin = pd.Series(df["volume"].values).groupby(idx).sum()
    vol_by_bin = vol_by_bin.reindex(range(bins), fill_value=0)
    poc_bin = vol_by_bin.idxmax()
    poc_price = (edges[poc_bin] + edges[poc_bin + 1]) / 2
    mean_v = vol_by_bin.mean()
    hvn = [(edges[i] + edges[i + 1]) / 2 for i in vol_by_bin.index if vol_by_bin[i] > mean_v * 1.3]
    lvn = [(edges[i] + edges[i + 1]) / 2 for i in vol_by_bin.index if vol_by_bin[i] < mean_v * 0.5]
    return {"poc": poc_price, "hvn": hvn, "lvn": lvn, "edges": edges, "profile": vol_by_bin}
