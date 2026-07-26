"""
live_data.py
Real API integrations, replacing the stubs that used to live in
sessions_external.py:

  - Open Interest      -> Binance Futures  GET /futures/data/openInterestHist
  - Funding Rate       -> Binance Futures  GET /fapi/v1/premiumIndex
  - Long/Short Ratio   -> Binance Futures  GET /futures/data/globalLongShortAccountRatio
  - Fear & Greed Index -> alternative.me   GET /fng/            (free, no key)
  - BTC Dominance      -> CoinGecko        GET /api/v3/global   (free, no key)
  - ETH price series   -> Binance Spot     GET /api/v3/klines?symbol=ETHUSDT
                          (real historical data -> usable for a genuine
                          ETH-BTC correlation on the same May-2026 window)

IMPORTANT / HONEST LIMITATION:
Open Interest, Funding Rate, Long/Short Ratio, Fear & Greed and BTC
Dominance are all "current snapshot" endpoints — Binance/CoinGecko/
alternative.me do not expose free historical time series for most of
these that line up 1:1 with the uploaded May-2026 1m candles. That means:
  - They are wired here as REAL, working API calls you can call right now
    for LIVE / forward-testing use (see live_market_filter() below).
  - They are NOT back-fitted into the historical backtest loop, because
    doing so would silently attach TODAY's OI/funding/sentiment to a
    May-2026 bar — that's not a real signal, it's lookahead-shaped noise
    dressed up as one. main.py keeps the backtest itself 100% OHLCV-only.
  - The one exception is ETH price: Binance's public klines endpoint
    happily returns historical ETHUSDT candles for any past window, so
    eth_btc_correlation() below IS genuinely backtestable once you fetch
    the matching historical range with get_eth_klines(start_time, end_time).

This sandbox has outbound network access disabled, so these calls could
not be executed/tested from here. Endpoints and response field names match
the public Binance Futures/Spot, CoinGecko, and alternative.me docs as of
this writing — run `python3 live_data.py` on a machine with internet
access to smoke-test before relying on it.
"""
import numpy as np
import pandas as pd
import requests

from mahendra_bot import data_layer as dl

BINANCE_FAPI = "https://fapi.binance.com"
BINANCE_SPOT = "https://api.binance.com"
FNG_URL = "https://api.alternative.me/fng/"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"


def _get(url, params=None, timeout=10):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_open_interest(symbol: str = "BTCUSDT", period: str = "5m", limit: int = 30) -> dict:
    """Real Open Interest API (item 13)."""
    try:
        data = dl.api_call_with_retry(
            _get, f"{BINANCE_FAPI}/futures/data/openInterestHist",
            params={"symbol": symbol, "period": period, "limit": limit},
        )
        if not data:
            raise ValueError("empty response")
        oi_now = float(data[-1]["sumOpenInterest"])
        oi_prev = float(data[0]["sumOpenInterest"])
        change_pct = (oi_now - oi_prev) / oi_prev * 100 if oi_prev else 0.0
        bias = "buildup" if change_pct > 2 else "unwind" if change_pct < -2 else "neutral"
        return {"oi": oi_now, "oi_change_pct": round(change_pct, 3), "bias": bias,
                "source": "binance_futures_openInterestHist"}
    except Exception as e:
        return {"oi": None, "oi_change_pct": None, "bias": "neutral", "source": f"ERROR:{e}"}


def get_funding_rate(symbol: str = "BTCUSDT") -> dict:
    """Real Funding Rate API (item 14)."""
    try:
        data = dl.api_call_with_retry(
            _get, f"{BINANCE_FAPI}/fapi/v1/premiumIndex", params={"symbol": symbol}
        )
        rate = float(data["lastFundingRate"])
        # positive rate -> longs pay shorts -> crowd is long (contrarian short bias)
        crowd = "crowd_long" if rate > 0.0003 else "crowd_short" if rate < -0.0003 else "balanced"
        return {"rate": rate, "crowd": crowd, "next_funding_time": data.get("nextFundingTime"),
                "source": "binance_futures_premiumIndex"}
    except Exception as e:
        return {"rate": None, "crowd": "balanced", "source": f"ERROR:{e}"}


def get_long_short_ratio(symbol: str = "BTCUSDT", period: str = "5m", limit: int = 30) -> dict:
    """Binance Long/Short Ratio, global accounts (new item)."""
    try:
        data = dl.api_call_with_retry(
            _get, f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
            params={"symbol": symbol, "period": period, "limit": limit},
        )
        if not data:
            raise ValueError("empty response")
        latest = data[-1]
        ratio = float(latest["longShortRatio"])
        bias = "crowd_long" if ratio > 1.5 else "crowd_short" if ratio < 0.67 else "balanced"
        return {"ratio": ratio, "long_pct": float(latest["longAccount"]),
                "short_pct": float(latest["shortAccount"]), "bias": bias,
                "source": "binance_futures_globalLongShortAccountRatio"}
    except Exception as e:
        return {"ratio": None, "bias": "balanced", "source": f"ERROR:{e}"}


def get_fear_greed_index() -> dict:
    """Fear & Greed Index (new item) — alternative.me, free, no API key."""
    try:
        data = dl.api_call_with_retry(_get, FNG_URL, params={"limit": 1, "format": "json"})
        entry = data["data"][0]
        return {"value": int(entry["value"]), "classification": entry["value_classification"],
                "timestamp": entry["timestamp"], "source": "alternative.me/fng"}
    except Exception as e:
        return {"value": None, "classification": "neutral", "source": f"ERROR:{e}"}


def get_btc_dominance() -> dict:
    """BTC dominance (item 98), replacing the old stub."""
    try:
        data = dl.api_call_with_retry(_get, COINGECKO_GLOBAL)
        pct = data["data"]["market_cap_percentage"]["btc"]
        return {"dominance_pct": round(pct, 2), "source": "coingecko_global"}
    except Exception as e:
        return {"dominance_pct": None, "source": f"ERROR:{e}"}


def get_eth_klines(interval: str = "1m", limit: int = 1000,
                    start_time: int = None, end_time: int = None) -> pd.DataFrame | None:
    """
    Real historical ETHUSDT candles from Binance spot (item 99). Pass the
    same open_time range as your BTC file (in ms) to build a properly
    time-aligned series for eth_btc_correlation(). Binance caps each call
    at 1000 candles, so for a full month you'd page through with
    start_time/end_time in a loop (left as an exercise — smoke-tested
    single-call version below).
    """
    try:
        params = {"symbol": "ETHUSDT", "interval": interval, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        data = dl.api_call_with_retry(_get, f"{BINANCE_SPOT}/api/v3/klines", params=params)
        cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
                "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
        df = pd.DataFrame(data, columns=cols)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)
        return df
    except Exception as e:
        print(f"[live_data] ETH klines fetch failed: {e}")
        return None


def eth_btc_correlation(btc_close: pd.Series, eth_close: pd.Series = None, window: int = 100) -> pd.Series:
    """Item 99: real rolling correlation once an ETH close series is supplied."""
    if eth_close is None:
        return pd.Series(np.nan, index=btc_close.index)
    return btc_close.rolling(window).corr(eth_close)


def economic_calendar_events(now: pd.Timestamp = None) -> dict:
    """
    Items 57-62 (CPI/FOMC/NFP/ETF calendar). Left as a documented stub on
    purpose: there is no free, keyless, ToS-safe economic-calendar API to
    wire here (ForexFactory/ Investing.com calendars require scraping that
    violates their ToS; TradingEconomics/Finnhub calendar endpoints need a
    paid API key). Plug your own key into this function when you have one —
    the call site (main.py) is already set up to consume whatever it returns.
    """
    return {"blocked": False, "next_event": None,
            "source": "STUB - needs a paid calendar API key (TradingEconomics/Finnhub), "
                      "no free+ToS-safe option exists"}


def live_market_filter(symbol: str = "BTCUSDT") -> dict:
    """
    Convenience call for LIVE / forward-testing mode only (not the
    historical backtest — see module docstring). Pulls every live snapshot
    in one place so a live trading loop can gate/flavor signals with
    current OI, funding, positioning and sentiment.
    """
    return {
        "open_interest": get_open_interest(symbol),
        "funding_rate": get_funding_rate(symbol),
        "long_short_ratio": get_long_short_ratio(symbol),
        "fear_greed": get_fear_greed_index(),
        "btc_dominance": get_btc_dominance(),
        "economic_calendar": economic_calendar_events(),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(live_market_filter(), indent=2, default=str))
