"""
sessions_external.py
- Session / kill-zone filters (63-67): computable directly from timestamps.
- External data (13, 14, 57-62, 98-99): now backed by real API calls in
  live_data.py instead of stubs (Binance futures funding/OI/long-short,
  alternative.me Fear & Greed, CoinGecko BTC dominance). The functions below
  are thin wrappers kept for backward compatibility with existing call
  sites/imports; they just forward to live_data.py. See live_data.py's
  module docstring for the honest caveat: these are LIVE snapshot endpoints,
  not historical series, so they're wired for live/forward-testing use, not
  back-fitted into the May-2026 historical backtest.
"""
import pandas as pd
import numpy as np

import live_data as ld


def utc_hour(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["open_time"], unit="us").dt.hour


def session_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Items 63-66: kill zones, defined in UTC."""
    h = utc_hour(df)
    london = (h >= 7) & (h < 10)
    ny = (h >= 12) & (h < 15)
    overlap = (h >= 12) & (h < 13)  # London-NY overlap window, UTC
    asian = (h >= 0) & (h < 7)
    return pd.DataFrame(
        {"london_kz": london, "ny_kz": ny, "london_ny_overlap": overlap, "asian_session": asian}
    )


def session_volatility_rating(df: pd.DataFrame, atr_series: pd.Series) -> pd.Series:
    """Item 67: rate each bar's session-relative volatility percentile (0-100)."""
    sess = session_filters(df)
    label = np.select(
        [sess["london_ny_overlap"], sess["london_kz"], sess["ny_kz"], sess["asian_session"]],
        ["overlap", "london", "ny", "asian"],
        default="off_session",
    )
    label = pd.Series(label, index=df.index)
    rating = atr_series.groupby(label).rank(pct=True) * 100
    return rating, label


# --------------------------------------------------------------------- #
# EXTERNAL / LIVE-DATA — real API calls (live_data.py), for live/forward
# trading. NOT applied inside the historical May-2026 backtest loop (see
# live_data.py docstring for why).
# --------------------------------------------------------------------- #
def funding_rate_filter(symbol: str = "BTCUSDT") -> dict:
    """Item 14: real Binance Futures funding rate."""
    return ld.get_funding_rate(symbol)


def open_interest_filter(symbol: str = "BTCUSDT") -> dict:
    """Item 13: real Binance Futures open interest, build-up/unwind vs its own recent history."""
    return ld.get_open_interest(symbol)


def long_short_ratio_filter(symbol: str = "BTCUSDT") -> dict:
    """New item: real Binance long/short account ratio."""
    return ld.get_long_short_ratio(symbol)


def fear_greed_filter() -> dict:
    """New item: real Fear & Greed Index (alternative.me)."""
    return ld.get_fear_greed_index()


def high_impact_news_filter(now: pd.Timestamp = None) -> dict:
    """
    Items 57-62 (CPI/FOMC/NFP/ETF/general calendar). Still a documented stub
    — no free+ToS-safe economic-calendar API exists; see live_data.py's
    economic_calendar_events() for why and where to plug a paid key.
    """
    return ld.economic_calendar_events(now)


def btc_dominance_filter() -> dict:
    """Item 98: real BTC dominance from CoinGecko's global-metrics endpoint."""
    return ld.get_btc_dominance()


def eth_btc_correlation(btc_close: pd.Series, eth_close: pd.Series = None, window: int = 100) -> pd.Series:
    """
    Item 99. If an ETHUSDT close series is supplied (e.g. from
    live_data.get_eth_klines(start_time=..., end_time=...) for the matching
    historical window), computes a real rolling correlation; otherwise NaNs.
    """
    return ld.eth_btc_correlation(btc_close, eth_close, window)
