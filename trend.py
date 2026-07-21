from indicators import ema


def get_trend(close_prices):
    price = round(close_prices[-1], 6)

    ema20 = ema(close_prices, 20)
    ema50 = ema(close_prices, 50)
    ema200 = ema(close_prices, 200)

    # Strong Bullish
    if price > ema20 > ema50 > ema200:
        return "Strong Bullish"

    # Bullish
    if ema20 > ema50 > ema200:
        return "Bullish"

    # Strong Bearish
    if price < ema20 < ema50 < ema200:
        return "Strong Bearish"

    # Bearish
    if ema20 < ema50 < ema200:
        return "Bearish"

    # Weak Bullish
    if price > ema20 and ema20 > ema50:
        return "Weak Bullish"

    # Weak Bearish
    if price < ema20 and ema20 < ema50:
        return "Weak Bearish"

    return "Sideways"
