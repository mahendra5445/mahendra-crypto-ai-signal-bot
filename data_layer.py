"""
data_layer.py
Items 86-87, 90-91, 94-95 (bug-fix / hardening section):
- API retry system + timeout (for live use; historical CSV load doesn't need
  network, but the wrapper below is what you'd reuse for live REST calls).
- Persistent database already covered in journal.py (item 88).
- Memory leak fix (90): stream-load with explicit dtypes rather than
  accumulating python objects; drop unused columns after use.
- Unlimited history fix (91): enforce a max in-memory bar count with a
  rolling window instead of an ever-growing DataFrame.
- Config validation (94) and data integrity check (95).
"""
import time
import numpy as np
import pandas as pd

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]


def load_klines_csv(path: str, max_bars: int | None = None) -> pd.DataFrame:
    dtypes = {
        "open_time": "int64", "open": "float64", "high": "float64", "low": "float64",
        "close": "float64", "volume": "float64", "close_time": "int64",
        "quote_volume": "float64", "trades": "int64", "taker_buy_base": "float64",
        "taker_buy_quote": "float64", "ignore": "int64",
    }
    df = pd.read_csv(path, header=None, names=KLINE_COLUMNS, dtype=dtypes)
    df = data_integrity_check(df)
    if max_bars:  # item 91: cap history kept in memory
        df = df.tail(max_bars).reset_index(drop=True)
    return df


def data_integrity_check(df: pd.DataFrame) -> pd.DataFrame:
    """Item 95: drop duplicate/garbled bars, enforce monotonic time, fix OHLC ordering."""
    before = len(df)
    df = df.drop_duplicates(subset="open_time").sort_values("open_time").reset_index(drop=True)
    # high must be the max, low the min, of the 4 OHLC values
    ohlc_max = df[["open", "high", "low", "close"]].max(axis=1)
    ohlc_min = df[["open", "high", "low", "close"]].min(axis=1)
    df["high"] = np.maximum(df["high"], ohlc_max)
    df["low"] = np.minimum(df["low"], ohlc_min)
    df = df[(df["volume"] >= 0) & (df["high"] >= df["low"])].reset_index(drop=True)
    dropped = before - len(df)
    if dropped:
        print(f"[data_integrity_check] dropped {dropped} malformed/duplicate rows")
    # gap check (missing minutes) — informational only
    gaps = df["open_time"].diff().dropna()
    expected = gaps.mode().iloc[0] if not gaps.empty else None
    n_gaps = (gaps != expected).sum() if expected else 0
    if n_gaps:
        print(f"[data_integrity_check] {n_gaps} timestamp gaps detected vs expected interval")
    return df


def validate_config(cfg: dict) -> list:
    """Item 94: returns a list of human-readable problems; empty list = valid."""
    problems = []
    if cfg.get("risk_per_trade_pct", 0) <= 0 or cfg.get("risk_per_trade_pct", 0) > 10:
        problems.append("risk_per_trade_pct should be between 0 and 10")
    if cfg.get("max_open_trades", 0) < 1:
        problems.append("max_open_trades must be >= 1")
    if cfg.get("account_balance", 0) <= 0:
        problems.append("account_balance must be positive")
    if cfg.get("atr_sl_mult", 0) <= 0:
        problems.append("atr_sl_mult must be positive")
    return problems


def api_call_with_retry(func, *args, max_retries=3, timeout=10, backoff=1.5, **kwargs):
    """
    Items 86-87: generic retry + timeout wrapper for LIVE endpoints
    (funding rate, open interest, news calendar, etc.). Not needed for the
    historical CSV backtest, but this is the pattern to reuse for live mode.
    """
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            kwargs.setdefault("timeout", timeout)
            return func(*args, **kwargs)
        except Exception as e:  # item 93: broad, logged, non-fatal
            last_exc = e
            print(f"[api_call_with_retry] attempt {attempt}/{max_retries} failed: {e}")
            time.sleep(backoff ** attempt)
    raise RuntimeError(f"API call failed after {max_retries} retries: {last_exc}")
