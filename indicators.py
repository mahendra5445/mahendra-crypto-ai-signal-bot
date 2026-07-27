import pandas as pd


def ema(values, period):
    return round(pd.Series(values).ewm(span=period, adjust=False).mean().iloc[-1], 6)


def rsi(values, period=14):
    close = pd.Series(values)
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    value = (100 - (100 / (1 + rs))).iloc[-1]
    # BUG FIX: bilkul flat market mein gain=0 aur loss=0 → 0/0 = NaN.
    # NaN aage har comparison ko False karta hai aur Telegram mein
    # "RSI : nan" dikhta hai. Neutral 50 return karna sahi fallback hai.
    if pd.isna(value):
        return 50.0
    return round(value, 2)


def macd(values):
    close = pd.Series(values)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()
    return {
        "macd": round(macd_line.iloc[-1], 6),
        "signal": round(signal.iloc[-1], 6),
        "trend": "Bullish" if macd_line.iloc[-1] >= signal.iloc[-1] else "Bearish",
    }


def atr(high, low, close, period=14):
    high = pd.Series(high)
    low = pd.Series(low)
    close = pd.Series(close)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return round(tr.rolling(period).mean().iloc[-1], 6)


def adx(high, low, close, period=14):
    high = pd.Series(high)
    low = pd.Series(low)
    close = pd.Series(close)
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atrv = tr.rolling(period).mean()
    plus = 100 * plus_dm.rolling(period).mean() / atrv
    minus = 100 * minus_dm.rolling(period).mean() / atrv
    # BUG FIX: jab plus + minus = 0 ho (price bilkul flat ho) to division
    # by zero se NaN aata tha. replace(0, NaN) se us row ka dx=NaN hoga
    # jo rolling mean mein safely ignore ho jaata hai.
    denom = (plus + minus).replace(0, float("nan"))
    dx = ((plus - minus).abs() / denom) * 100
    return round(dx.rolling(period).mean().iloc[-1], 2)


def trend_strength(adx_value):
    if adx_value >= 40:
        return "Very Strong"
    if adx_value >= 25:
        return "Strong"
    if adx_value >= 20:
        return "Moderate"
    return "Weak"


def vwap(high, low, close, volume):
    tp = (pd.Series(high) + pd.Series(low) + pd.Series(close)) / 3
    vol = pd.Series(volume)
    return round(((tp * vol).cumsum() / vol.cumsum()).iloc[-1], 6)


def bollinger_bands(values, period=20, std_dev=2):
    s = pd.Series(values)
    mid = s.rolling(period).mean()
    # ddof=0 matches TradingView population std (info-only BB path)
    std = s.rolling(period).std(ddof=0)
    return {
        "upper": round((mid + std * std_dev).iloc[-1], 6),
        "middle": round(mid.iloc[-1], 6),
        "lower": round((mid - std * std_dev).iloc[-1], 6),
    }


def bollinger_signal(close, high, low, period=20, std_dev=2):
    """
    'Bullish Bounce'  -> last candle poked below the lower band and closed
                          back inside it (reversal off support)
    'Bearish Rejection' -> last candle poked above the upper band and closed
                          back inside it (rejection off resistance)
    'None' otherwise
    """
    bb = bollinger_bands(close, period, std_dev)
    last_close, last_low, last_high = close[-1], low[-1], high[-1]

    if last_low <= bb["lower"] and last_close > bb["lower"]:
        return "Bullish Bounce"
    if last_high >= bb["upper"] and last_close < bb["upper"]:
        return "Bearish Rejection"
    return "None"


def atr_moving_average(high, low, close, atr_period=14, ma_period=20):
    """Average of the ATR series itself - used to confirm volatility is
    expanding (ATR rising above its own average) rather than contracting."""
    high_s = pd.Series(high)
    low_s = pd.Series(low)
    close_s = pd.Series(close)
    tr = pd.concat([
        high_s - low_s,
        (high_s - close_s.shift()).abs(),
        (low_s - close_s.shift()).abs(),
    ], axis=1).max(axis=1)
    atr_series = tr.rolling(atr_period).mean()
    return round(atr_series.rolling(ma_period).mean().iloc[-1], 6)


def supertrend(high, low, close, period=10, multiplier=3):
    """
    Proper stateful Supertrend calculation (NumPy loop for H-016 performance;
    algorithm unchanged from the prior pandas iloc version).
    """
    import numpy as np

    high_a = np.asarray(high, dtype="float64")
    low_a = np.asarray(low, dtype="float64")
    close_a = np.asarray(close, dtype="float64")
    n = len(close_a)

    tr = np.maximum(high_a - low_a, np.maximum(
        np.abs(high_a - np.roll(close_a, 1)),
        np.abs(low_a - np.roll(close_a, 1)),
    ))
    tr[0] = high_a[0] - low_a[0]

    atr_series = pd.Series(tr).rolling(period).mean().to_numpy()

    hl2 = (high_a + low_a) / 2.0
    basic_upper = hl2 + multiplier * atr_series
    basic_lower = hl2 - multiplier * atr_series

    start = None
    for i in range(n):
        if not np.isnan(atr_series[i]):
            start = i
            break
    if start is None or start >= n - 1:
        return {"trend": "Neutral", "value": round(float(close_a[-1]), 6)}

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    trend = [None] * n
    trend[start] = "Bullish" if close_a[start] >= hl2[start] else "Bearish"

    for i in range(start + 1, n):
        if basic_upper[i] < final_upper[i - 1] or close_a[i - 1] > final_upper[i - 1]:
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = final_upper[i - 1]

        if basic_lower[i] > final_lower[i - 1] or close_a[i - 1] < final_lower[i - 1]:
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = final_lower[i - 1]

        if trend[i - 1] == "Bullish":
            trend[i] = "Bearish" if close_a[i] < final_lower[i] else "Bullish"
        else:
            trend[i] = "Bullish" if close_a[i] > final_upper[i] else "Bearish"

    last_trend = trend[-1]
    last_value = final_lower[-1] if last_trend == "Bullish" else final_upper[-1]
    return {"trend": last_trend, "value": round(float(last_value), 6)}
