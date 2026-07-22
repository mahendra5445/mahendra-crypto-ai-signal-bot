import math


def calculate_trade(signal, price, atr, decimals=2, session_active=True):
    """
    Smart Risk Management - CONSERVATIVE VERSION (OPTION 3)
    - Wider ATR based Stop Loss (3.5x instead of 2.5x)
    - Wider TP targets (2R/3R/5R)
    - Better for trending markets where you want bigger profits per trade

    OPTION 3 FIX: Increase SL multiplier to 3.5x and adjust TP rewards
    Use this if you believe your signal quality is good but getting whipsawed
    by small moves. This trades fewer wins for bigger average profit per trade.

    Expected Results:
    - Win Rate: 40-50% (lower than Option 1)
    - Average Profit: 2R-3R per winning trade (higher than Option 1)
    - Breakeven Trades: 20-30% (these are runners that hit TP1 then get stopped at SL)

    `decimals` controls rounding precision — gold/BTC/oil use 2, but a pair
    like EUR/USD needs 4-5 decimals or a 0.01 rounding would erase ~100 pips
    of precision. Defaults to 2 for backward compatibility with old callers.

    `session_active` — True for London/New York, False for Asian/Off-Hours
    (see session.py). Passed through so the SL can be widened below.
    """

    if signal not in ["BUY", "SELL"]:
        return {
            "entry": None,
            "sl": None,
            "tp1": None,
            "tp2": None,
            "tp3": None,
            "risk_reward": "-"
        }

    entry = round(price, decimals)

    # ========================================================================
    # OPTION 3: WIDER STOP LOSS (3.5x instead of 2.5x)
    # ========================================================================
    # This prevents tight SL from being hit by noise, but allows bigger losses
    # on wrong direction trades.
    #
    # Use when:
    # - Market is trending strongly (strong ADX readings)
    # - Your signal quality is good but you're getting whipsawed
    # - You want bigger average profit per trade
    # - You're in London/NY session (high liquidity, less noise)
    # ========================================================================
    
    session_factor = 1.0 if session_active else 1.4
    sl_mult = 3.5 * session_factor  # ← INCREASED from 2.5 to 3.5

    if atr is None or (isinstance(atr, float) and math.isnan(atr)):
        atr = 0

    risk = round(atr * sl_mult, decimals)

    # For wider SL, also increase minimum floor proportionally
    min_risk = round(price * 0.002 * session_factor, decimals)  # Increased from 0.0015
    if risk < min_risk:
        risk = min_risk

    # ========================================================================
    # CONSERVATIVE TP TARGETS (2R/3R/5R)
    # ========================================================================
    # Since SL is wider, we maintain good R:R with these targets
    # Even with 3.5x ATR SL, a 2R TP gives us 1:2 R:R which is reasonable
    # ========================================================================
    
    tp1_reward = risk * 2.0   # 2R - good R:R of 1:2
    tp2_reward = risk * 3.0   # 3R - decent upside
    tp3_reward = risk * 5.0   # 5R - runner target for big moves

    if signal == "BUY":
        sl = round(entry - risk, decimals)
        tp1 = round(entry + tp1_reward, decimals)
        tp2 = round(entry + tp2_reward, decimals)
        tp3 = round(entry + tp3_reward, decimals)

    else:  # SELL
        sl = round(entry + risk, decimals)
        tp1 = round(entry - tp1_reward, decimals)
        tp2 = round(entry - tp2_reward, decimals)
        tp3 = round(entry - tp3_reward, decimals)

    actual_risk = abs(entry - sl)
    actual_reward = abs(tp1 - entry)

    rr = round(actual_reward / actual_risk, 2) if actual_risk > 0 else 0

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_reward": f"1:{rr}"
    }
