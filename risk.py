"""
Risk model: ATR-based stop, targets built off the risk distance so the
posted risk-reward is always the real one.
"""

import math

from config import MIN_RISK_PCT

# Targets as multiples of the risk distance (R).
TP1_R = 1.2
TP2_R = 2.0
TP3_R = 3.0

ATR_MULT = 2.5          # stop = 2.5 x ATR(5m)
ASIAN_WIDEN = 1.4       # widen stop + floor by 40% in thin sessions


def calculate_trade(signal, price, atr, decimals=2, session_active=True):
    """
    `decimals` MUST come from config.effective_decimals(asset, price) —
    a hardcoded per-coin value silently destroys the risk model on
    low-priced coins (a $6 coin at 2 decimals gets a one-cent stop, so a
    1.2R target rounds to exactly 1.0R).

    `session_active` — True for London/New York, False for Asian/Off-Hours.
    Thin sessions get a wider stop: same ATR, wider real spread and noisier
    wicks, so a stop sized for London liquidity gets tagged by noise alone.
    """

    if signal not in ("BUY", "SELL"):
        return {
            "entry": None, "sl": None, "tp1": None, "tp2": None, "tp3": None,
            "risk_distance": None, "risk_reward": "-",
        }

    entry = round(price, decimals)

    session_factor = 1.0 if session_active else ASIAN_WIDEN
    sl_mult = ATR_MULT * session_factor

    # ATR arrives as NaN if upstream data had a gap. `nan <= 0` is False in
    # Python, so an unguarded NaN would flow straight through and turn every
    # SL/TP in the Telegram message into "nan".
    if atr is None or (isinstance(atr, float) and math.isnan(atr)):
        atr = 0

    risk = round(atr * sl_mult, decimals)

    # Floor the stop at MIN_RISK_PCT of price so a quiet market can't produce
    # a stop that sits inside normal spread and noise.
    min_risk = round(price * MIN_RISK_PCT * session_factor, decimals)
    if risk < min_risk:
        risk = min_risk

    tp1_reward = risk * TP1_R
    tp2_reward = risk * TP2_R
    tp3_reward = risk * TP3_R

    if signal == "BUY":
        sl  = round(entry - risk, decimals)
        tp1 = round(entry + tp1_reward, decimals)
        tp2 = round(entry + tp2_reward, decimals)
        tp3 = round(entry + tp3_reward, decimals)
    else:
        sl  = round(entry + risk, decimals)
        tp1 = round(entry - tp1_reward, decimals)
        tp2 = round(entry - tp2_reward, decimals)
        tp3 = round(entry - tp3_reward, decimals)

    actual_risk   = abs(entry - sl)
    actual_reward = abs(tp1 - entry)
    rr = round(actual_reward / actual_risk, 2) if actual_risk > 0 else 0

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_distance": actual_risk,
        "risk_reward": f"1:{rr}",
    }
