"""
regime.py
Items 96-97: Market Regime (trend/range) and Volatility Regime Detection.
"""
import numpy as np
import pandas as pd
from .indicators import adx, atr


def market_regime(df: pd.DataFrame, adx_period=14, trend_thresh=22) -> pd.Series:
    a = adx(df, adx_period)
    return pd.Series(np.where(a >= trend_thresh, "trend", "range"), index=df.index)


def volatility_regime(df: pd.DataFrame, period=14, lookback=200) -> pd.Series:
    a = atr(df, period)
    pct_rank = a.rolling(lookback).apply(lambda x: (x.iloc[-1] > x).mean() * 100, raw=False)
    return pd.cut(
        pct_rank, bins=[-1, 33, 66, 101], labels=["low_vol", "normal_vol", "high_vol"]
    )
