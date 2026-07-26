"""
sessions_external.py
- Session / kill-zone filters (63-67): computable directly from timestamps.
- External data placeholders (13, 14, 57-62, 98-99): these need LIVE feeds
  (Binance futures funding/OI API, an economic-calendar API, a BTC dominance
  source, an ETH price feed). They CANNOT be derived from a historical 1m
  OHLCV file. Each function below is a clearly-marked stub returning a
  neutral value, with a TODO for wiring a real API key/endpoint.
"""
import pandas as pd
import numpy as np


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
# EXTERNAL / LIVE-DATA STUBS  (require API wiring — not derivable from
# the uploaded historical CSV). Replace the body of each function with a
# real API call before going live.
# --------------------------------------------------------------------- #
def funding_rate_filter(symbol: str = "BTCUSDT") -> dict:
    """
    Item 14. TODO: call Binance Futures `GET /fapi/v1/fundingRate` or
    `premiumIndex` and return {'rate': float, 'bias': 'long'|'short'|'neutral'}.
    """
    return {"rate": None, "bias": "neutral", "source": "STUB - wire Binance Futures API"}


def open_interest_filter(symbol: str = "BTCUSDT") -> dict:
    """
    Item 13. TODO: call Binance Futures `GET /futures/data/openInterestHist`
    and compare current OI vs its moving average to flag build-up/unwind.
    """
    return {"oi_change_pct": None, "bias": "neutral", "source": "STUB - wire Binance Futures API"}


def high_impact_news_filter(now: pd.Timestamp = None) -> dict:
    """
    Items 57-62 (CPI/FOMC/NFP/ETF/general calendar). TODO: call an economic
    calendar API (e.g. ForexFactory/TradingEconomics/Investing.com calendar)
    and block trading N minutes around red-folder events.
    """
    return {"blocked": False, "next_event": None, "source": "STUB - wire economic calendar API"}


def btc_dominance_filter() -> dict:
    """Item 98. TODO: pull BTC.D from CoinMarketCap/CoinGecko global-metrics endpoint."""
    return {"dominance_pct": None, "trend": "neutral", "source": "STUB - wire CoinGecko/CMC API"}


def eth_btc_correlation(btc_close: pd.Series, eth_close: pd.Series = None, window: int = 100) -> pd.Series:
    """
    Item 99. Correlation filter. If an ETHUSDT close series is supplied,
    computes a real rolling correlation; otherwise returns NaNs (no ETH data
    was in the uploaded file — only BTCUSDT).
    """
    if eth_close is None:
        return pd.Series(np.nan, index=btc_close.index)
    return btc_close.rolling(window).corr(eth_close)
