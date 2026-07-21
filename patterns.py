"""
Candlestick Pattern Detection
Detects: Bullish Engulfing, Bearish Engulfing, Pin Bar, Doji,
Morning Star, Evening Star
Uses the last few candles (open, high, low, close).
"""


def _body(o, c):
    return abs(c - o)


def _range(h, l):
    return max(h - l, 1e-9)


def detect_pattern(open_, high, low, close):
    """
    Returns (pattern_name, direction) using the most recent candles.
    direction is 'Bullish', 'Bearish', or None.
    """

    if len(close) < 3:
        return "None", None

    o1, h1, l1, c1 = open_[-1], high[-1], low[-1], close[-1]
    o2, h2, l2, c2 = open_[-2], high[-2], low[-2], close[-2]
    o3, h3, l3, c3 = open_[-3], high[-3], low[-3], close[-3]

    body1 = _body(o1, c1)
    range1 = _range(h1, l1)

    # ==========================
    # Doji (very small body vs range)
    # ==========================
    if body1 <= range1 * 0.1:
        return "Doji", None

    # ==========================
    # Bullish Engulfing
    # ==========================
    if c2 < o2 and c1 > o1 and c1 >= o2 and o1 <= c2:
        return "Bullish Engulfing", "Bullish"

    # ==========================
    # Bearish Engulfing
    # ==========================
    if c2 > o2 and c1 < o1 and o1 >= c2 and c1 <= o2:
        return "Bearish Engulfing", "Bearish"

    # ==========================
    # Pin Bar (long wick, small body near one end)
    # ==========================
    upper_wick = h1 - max(o1, c1)
    lower_wick = min(o1, c1) - l1

    if lower_wick >= body1 * 2 and upper_wick <= body1:
        return "Bullish Pin Bar", "Bullish"

    if upper_wick >= body1 * 2 and lower_wick <= body1:
        return "Bearish Pin Bar", "Bearish"

    # ==========================
    # Morning Star (bearish, small body, bullish - 3 candle reversal)
    # ==========================
    if (
        c3 < o3
        and _body(o2, c2) <= _range(h2, l2) * 0.4
        and c1 > o1
        and c1 > ((o3 + c3) / 2)
    ):
        return "Morning Star", "Bullish"

    # ==========================
    # Evening Star (bullish, small body, bearish - 3 candle reversal)
    # ==========================
    if (
        c3 > o3
        and _body(o2, c2) <= _range(h2, l2) * 0.4
        and c1 < o1
        and c1 < ((o3 + c3) / 2)
    ):
        return "Evening Star", "Bearish"

    return "None", None
