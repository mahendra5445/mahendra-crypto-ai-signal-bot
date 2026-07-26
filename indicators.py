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
    std = s.rolling(period).std()
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
    Proper stateful Supertrend calculation.

    The old version compared only the latest candle to a single
    upper/lower band and defaulted to "Bullish" any time price sat
    between the bands (which is most of the time) - that silently
    biased every signal toward BUY regardless of real trend.

    This version walks the whole series, flips trend only when price
    actually closes beyond the trailing band (the real Supertrend
    rule), and has no directional default.
    """
    high_s = pd.Series(high)
    low_s = pd.Series(low)
    close_s = pd.Series(close)

    tr = pd.concat([
        high_s - low_s,
        (high_s - close_s.shift()).abs(),
        (low_s - close_s.shift()).abs(),
    ], axis=1).max(axis=1)
    atr_series = tr.rolling(period).mean()

    hl2 = (high_s + low_s) / 2
    basic_upper = hl2 + multiplier * atr_series
    basic_lower = hl2 - multiplier * atr_series

    start = atr_series.first_valid_index()
    if start is None or start >= len(close_s) - 1:
        # BUG FIX: pehle yahan "Bearish" return hota tha jab data kam hota
        # tha - comment khud keh raha tha "neutral, not Bullish" lekin code
        # Bearish bhej raha tha, jo SELL ki taraf bias deta tha.
        # "Neutral" return karne se strategy mein st_bull aur st_bear dono
        # False rahenge - na BUY ki taraf jhukao, na SELL ki taraf.
        return {"trend": "Neutral", "value": round(close[-1], 6)}

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    trend = ["Bullish"] * len(close_s)
    trend[start] = "Bullish" if close_s.iloc[start] >= hl2.iloc[start] else "Bearish"

    for i in range(start + 1, len(close_s)):
        if basic_upper.iloc[i] < final_upper.iloc[i - 1] or close_s.iloc[i - 1] > final_upper.iloc[i - 1]:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i - 1]

        if basic_lower.iloc[i] > final_lower.iloc[i - 1] or close_s.iloc[i - 1] < final_lower.iloc[i - 1]:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i - 1]

        if trend[i - 1] == "Bullish":
            trend[i] = "Bearish" if close_s.iloc[i] < final_lower.iloc[i] else "Bullish"
        else:
            trend[i] = "Bullish" if close_s.iloc[i] > final_upper.iloc[i] else "Bearish"

    last_trend = trend[-1]
    last_value = final_lower.iloc[-1] if last_trend == "Bullish" else final_upper.iloc[-1]
    return {"trend": last_trend, "value": round(last_value, 6)}
