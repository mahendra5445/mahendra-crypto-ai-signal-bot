def analyze_smart_money(high, low, close):
    """
    Smart Money Concepts v2.0
    """

    result = {
        "bos": False,
        "bos_direction": "None",
        "choch": False,
        "choch_direction": "None",
        "order_block": "None",
        "fvg": False,
        "liquidity": False,
        "liquidity_side": "None",
        "fake_breakout": False,
        "premium_discount": "Equilibrium",
        "smc_score": 0,
    }

    if len(close) < 10:
        return result

    # Break of Structure
    if close[-1] > max(close[-6:-1]):
        result["bos"] = True
        result["bos_direction"] = "Bullish"
        result["smc_score"] += 20
    elif close[-1] < min(close[-6:-1]):
        result["bos"] = True
        result["bos_direction"] = "Bearish"
        result["smc_score"] += 20

    # CHoCH
    if len(close) > 20:
        if close[-1] > close[-5] and close[-5] < close[-10]:
            result["choch"] = True
            result["choch_direction"] = "Bullish"
            result["smc_score"] += 15
        elif close[-1] < close[-5] and close[-5] > close[-10]:
            result["choch"] = True
            result["choch_direction"] = "Bearish"
            result["smc_score"] += 15

    # Order Block
    if close[-1] > close[-2]:
        result["order_block"] = "Bullish"
    elif close[-1] < close[-2]:
        result["order_block"] = "Bearish"

    # Fair Value Gap
    if high[-3] < low[-1] or low[-3] > high[-1]:
        result["fvg"] = True
        result["smc_score"] += 15

    # Liquidity Sweep
    if high[-1] > max(high[-6:-1]):
        result["liquidity"] = True
        result["liquidity_side"] = "Buy Side"
        result["smc_score"] += 20
    elif low[-1] < min(low[-6:-1]):
        result["liquidity"] = True
        result["liquidity_side"] = "Sell Side"
        result["smc_score"] += 20

    # Fake breakout
    if result["liquidity"] and result["bos"] and (
        result["bos_direction"] == "Bullish" and close[-1] < high[-2] or
        result["bos_direction"] == "Bearish" and close[-1] > low[-2]
    ):
        result["fake_breakout"] = True

    # Premium / Discount
    swing_high = max(high[-20:])
    swing_low = min(low[-20:])
    mid = (swing_high + swing_low) / 2

    if close[-1] > mid:
        result["premium_discount"] = "Premium"
    elif close[-1] < mid:
        result["premium_discount"] = "Discount"

    return result
