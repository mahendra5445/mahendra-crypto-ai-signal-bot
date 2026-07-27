from indicators import (
    ema,
    rsi,
    macd,
    atr,
    atr_moving_average,
    adx,
    trend_strength,
    vwap,
    bollinger_bands,
    bollinger_signal,
    supertrend,
)

from trend import get_trend
from risk import calculate_trade
from smart_money import analyze_smart_money
from patterns import detect_pattern
from session import get_current_session
from config import MIN_CONFIRMATIONS, MIN_SCORE

# ==========================
# MAHENDRA CRYPTO AI SIGNAL - SCORE WEIGHTS (total = 100)
# ==========================
# BUG FIX: these previously summed to 110 (15+10+15+10+10+10+10+5+15+10),
# not 100 as the comment claimed. buy_score/sell_score were then clamped
# with min(score, 100), so any setup scoring 90-110 raw all displayed as
# a flat "100/100" — real quality differences between a 90-point and a
# 110-point setup were invisible, and grade thresholds (A+ >=90) were
# effectively easier to hit than the 0-100 scale implied. Rebalanced
# proportionally so the weights actually sum to 100.
W_EMA = 14
W_ADX = 9
W_SUPERTREND = 14
W_VWAP = 9
W_MACD = 9
W_RSI = 9
W_VOLUME = 9
W_ATR = 4
W_MTF = 14
# H-006: liquidity demoted to info-only — weight no longer applied in score_side.
# Kept as a named constant for documentation / future opt-in only.
W_LIQUIDITY = 9

# MIN_SCORE and MIN_CONFIRMATIONS now come from config.py (env-tunable).
# Default MIN_CONFIRMATIONS is 9. Countable confirmations are 9 (no volume)
# or 11 (with volume); liquidity is informational only (H-005 / H-006).
SIGNAL_VALID_MINUTES = 8


def _empty_result(reason="Not enough candles"):
    return {
        "signal": "NO TRADE",
        "confidence": 0,
        "ai_score": 0,
        "grade": "-",
        "signal_tier": "-",
        "position_size": "-",
        "market_status": "-",
        "session": "-",
        "session_active": True,
        "trend_1m": "-",
        "trend_5m": "-",
        "trend_15m": "-",
        "trend_strength": "-",
        "ema_ok": False,
        "adx_ok": False,
        "vwap_ok": False,
        "supertrend_ok": False,
        "volume_ok": False,
        "atr_ok": False,
        "liquidity_ok": False,
        "macd": {"macd": 0, "signal": 0, "trend": "-"},
        "rsi": 0,
        "pattern": "None",
        "liquidity_sweep": "NO",
        "bollinger": "None",
        "buy_confirmations": 0,
        "sell_confirmations": 0,
        "reasons": [reason],
        "valid_minutes": SIGNAL_VALID_MINUTES,
        "atr_value": 0,
        "entry": None,
        "sl": None,
        "tp1": None,
        "tp2": None,
        "tp3": None,
        "risk_reward": "-",
    }


def get_signal(close, high, low, timeframes, volume=None, open_=None, decimals=2):

    if close is None or len(close) < 200:
        return _empty_result()

    # M-020: each MTF series needs enough bars for EMA200 inside get_trend
    for tf_name in ("1m", "5m", "15m"):
        series = (timeframes or {}).get(tf_name) or []
        if len(series) < 200:
            return _empty_result(f"Not enough {tf_name} candles")

    price = round(close[-1], decimals)

    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    ema200 = ema(close, 200)

    rsi_value = rsi(close)
    macd_value = macd(close)
    atr_value = atr(high, low, close)
    atr_ma_value = atr_moving_average(high, low, close)
    adx_value = adx(high, low, close)

    trend1 = get_trend(timeframes["1m"])
    trend5 = get_trend(timeframes["5m"])
    trend15 = get_trend(timeframes["15m"])
    trend_power = trend_strength(adx_value)

    if volume:
        vwap_value = vwap(high, low, close, volume)
    else:
        vwap_value = price

    bb = bollinger_bands(close)
    bb_signal = bollinger_signal(close, high, low)
    st = supertrend(high, low, close)
    smc = analyze_smart_money(high, low, close)

    session_name, session_active = get_current_session()

    if open_:
        pattern_name, pattern_direction = detect_pattern(open_, high, low, close)
    else:
        pattern_name, pattern_direction = "None", None

    # ==========================
    # 2. EMA FILTER
    # ==========================
    ema_bull = ema20 > ema50 > ema200
    ema_bear = ema20 < ema50 < ema200

    # ==========================
    # 3. ADX FILTER
    # ==========================
    adx_ok = adx_value >= 22

    # ==========================
    # 4. SUPERTREND
    # ==========================
    st_bull = st["trend"] == "Bullish"
    st_bear = st["trend"] == "Bearish"

    # ==========================
    # 5. VWAP
    # ==========================
    vwap_bull = price > vwap_value
    vwap_bear = price < vwap_value

    # ==========================
    # 6. RSI (widened bands)
    # ==========================
    rsi_bull = 52 <= rsi_value <= 75
    rsi_bear = 25 <= rsi_value <= 48

    # ==========================
    # 7. MACD (line vs signal + histogram)
    # ==========================
    histogram = round(macd_value["macd"] - macd_value["signal"], 4)
    macd_bull = macd_value["macd"] > macd_value["signal"] and histogram > 0
    macd_bear = macd_value["macd"] < macd_value["signal"] and histogram < 0

    # ==========================
    # 8. MULTI TIMEFRAME (2-of-3 agreement, includes Weak variants)
    # ==========================
    bullish_trends = {"Strong Bullish", "Bullish", "Weak Bullish"}
    bearish_trends = {"Strong Bearish", "Bearish", "Weak Bearish"}
    mtf_bull_count = sum(t in bullish_trends for t in [trend1, trend5, trend15])
    mtf_bear_count = sum(t in bearish_trends for t in [trend1, trend5, trend15])
    mtf_bull = mtf_bull_count >= 2
    mtf_bear = mtf_bear_count >= 2

    # ==========================
    # 9. VOLUME FILTER (current >= 85% of 20-candle average)
    # ==========================
    # H-005 / H-017: missing volume must NOT auto-pass. When volume data is
    # absent, volume checks are excluded from the confirmation denominator
    # and from score weight (Option B).
    volume_data_ok = bool(volume and len(volume) >= 20 and sum(volume[-20:]) > 0)
    volume_ok = False
    volume_spike_ok = False
    if volume_data_ok:
        recent_avg = sum(volume[-20:-1]) / len(volume[-20:-1])
        current_vol = volume[-1]
        volume_ok = recent_avg > 0 and current_vol >= recent_avg * 0.85
        # Keep 1.05 threshold when volume exists — preserves prior strategy
        # for assets that do report volume.
        volume_spike_ok = recent_avg > 0 and current_vol >= recent_avg * 1.05

    # ==========================
    # 10. ATR FILTER (scoring bonus only, not a hard gate)
    # ==========================
    atr_ok = bool(atr_ma_value) and atr_value > atr_ma_value

    # ==========================
    # NEW: CANDLE CONFIRMATION
    # The last CLOSED candle must actually close in the signal's
    # direction - bullish body (close > open) for BUY, bearish body
    # (close < open) for SELL. Without open prices we can't confirm,
    # so pass rather than block.
    # ==========================
    candle_bull_ok = True
    candle_bear_ok = True
    if open_:
        last_open, last_close = open_[-1], close[-1]
        candle_bull_ok = last_close > last_open
        candle_bear_ok = last_close < last_open

    # ==========================
    # 12. LIQUIDITY — informational only (H-006)
    # ==========================
    # Still computed for display / reasons context, but no longer a scored
    # confirmation or free +W_LIQUIDITY points.
    liquidity_ok = not smc["fake_breakout"] and not (smc["liquidity"] and not smc["bos"])

    # ==========================
    # 14. SESSION FILTER - info only now, not a hard gate
    # ==========================
    session_ok = True

    # ==========================
    # SCORE (only counts the side currently being evaluated)
    # ==========================
    def score_side(is_bull):
        s = 0
        s += W_EMA if (ema_bull if is_bull else ema_bear) else 0
        s += W_ADX if adx_ok else 0
        s += W_SUPERTREND if (st_bull if is_bull else st_bear) else 0
        s += W_VWAP if (vwap_bull if is_bull else vwap_bear) else 0
        s += W_MACD if (macd_bull if is_bull else macd_bear) else 0
        s += W_RSI if (rsi_bull if is_bull else rsi_bear) else 0
        if volume_data_ok:
            s += W_VOLUME if volume_ok else 0
        s += W_ATR if atr_ok else 0
        s += W_MTF if (mtf_bull if is_bull else mtf_bear) else 0
        # liquidity weight intentionally omitted (H-006)
        return s

    buy_score = score_side(True)
    sell_score = score_side(False)

    if pattern_direction == "Bullish":
        buy_score += 5
    elif pattern_direction == "Bearish":
        sell_score += 5

    # NOTE: was comparing against "London-New York Overlap" (hyphen) but
    # session.py actually returns "London + New York Overlap" (plus sign),
    # so the bonus never fired during the overlap window. Substring check
    # below matches "London", "New York", and the overlap string in one go.
    if "London" in session_name or "New York" in session_name:
        buy_score += 3
        sell_score += 3

    # BUG FIX (SL hit too early / low-quality Asian-session signals):
    # session_ok above is info-only, so the Asian/Off-Hours session no
    # longer blocks trades - but it never penalized them either, meaning a
    # thin-liquidity setup could score exactly the same as one during
    # London/NY. This soft penalty keeps low-liquidity setups tradeable
    # (matches the original intent of relaxing the hard session gate) but
    # requires them to clear a higher bar, and it's paired with the wider
    # SL that risk.py now applies for the same session_active=False case.
    if not session_active:
        buy_score -= 8
        sell_score -= 8

    # Scores are weighted to sum to 100 but bonuses (pattern +5, session +3)
    # can push them over - always cap at 100 (and floor at 0, since the
    # new low-liquidity penalty above can now push a very weak score negative).
    buy_score = max(0, min(buy_score, 100))
    sell_score = max(0, min(sell_score, 100))

    buy_confirmations_parts = [
        ema_bull, adx_ok, st_bull, vwap_bull, macd_bull,
        rsi_bull, atr_ok, mtf_bull, candle_bull_ok,
    ]
    sell_confirmations_parts = [
        ema_bear, adx_ok, st_bear, vwap_bear, macd_bear,
        rsi_bear, atr_ok, mtf_bear, candle_bear_ok,
    ]
    if volume_data_ok:
        buy_confirmations_parts.extend([volume_ok, volume_spike_ok])
        sell_confirmations_parts.extend([volume_ok, volume_spike_ok])

    buy_confirmations = sum(buy_confirmations_parts)
    sell_confirmations = sum(sell_confirmations_parts)

    # ==========================
    # 17. FINAL CONFIRMATION
    # ==========================
    buy_all_true = buy_confirmations >= MIN_CONFIRMATIONS
    sell_all_true = sell_confirmations >= MIN_CONFIRMATIONS

    def build_reasons(is_bull):
        """
        Build the reasons list from the SAME booleans used to count
        confirmations, so Telegram's displayed reasons always match the
        buy/sell confirmation count - no more hardcoded lists that claim
        e.g. "Volume OK" when volume_ok was actually False.
        """
        checks = [
            (ema_bull if is_bull else ema_bear,
             "EMA Bullish Stack" if is_bull else "EMA Bearish Stack"),
            (adx_ok, "ADX Strong"),
            (st_bull if is_bull else st_bear,
             "Bullish Supertrend" if is_bull else "Bearish Supertrend"),
            (vwap_bull if is_bull else vwap_bear,
             "Above VWAP" if is_bull else "Below VWAP"),
            (macd_bull if is_bull else macd_bear,
             "Bullish MACD" if is_bull else "Bearish MACD"),
            (rsi_bull if is_bull else rsi_bear, f"RSI Healthy ({rsi_value})"),
            (mtf_bull if is_bull else mtf_bear,
             "MTF Bullish Alignment" if is_bull else "MTF Bearish Alignment"),
            (atr_ok, "ATR Expansion"),
            (candle_bull_ok if is_bull else candle_bear_ok,
             "Bullish Candle Confirmed" if is_bull else "Bearish Candle Confirmed"),
        ]
        if volume_data_ok:
            checks.append((volume_ok, "Volume OK"))
            checks.append((volume_spike_ok, "Volume Spike"))
        # Liquidity is informational only (H-006)
        if liquidity_ok:
            checks.append((True, "Liquidity clean (info)"))
        elif smc["fake_breakout"] or (smc["liquidity"] and not smc["bos"]):
            checks.append((True, "Liquidity caution (info only — not gated)"))
        return [label for ok, label in checks if ok]

    reasons = []
    final_signal = "NO TRADE"

    buy_ready = buy_all_true and buy_score >= MIN_SCORE
    sell_ready = sell_all_true and sell_score >= MIN_SCORE

    # H-009: no systematic BUY bias when both sides qualify
    if buy_ready and sell_ready:
        if buy_score > sell_score:
            final_signal = "BUY"
            reasons = build_reasons(True)
        elif sell_score > buy_score:
            final_signal = "SELL"
            reasons = build_reasons(False)
        else:
            final_signal = "NO TRADE"
            reasons = ["NO TRADE - BUY and SELL tied"]
    elif buy_ready:
        final_signal = "BUY"
        reasons = build_reasons(True)
    elif sell_ready:
        final_signal = "SELL"
        reasons = build_reasons(False)
    else:
        checklist_bull = {
            "EMA": ema_bull, "ADX": adx_ok, "Supertrend": st_bull,
            "VWAP": vwap_bull, "RSI": rsi_bull, "MACD": macd_bull,
            "MTF": mtf_bull, "ATR": atr_ok,
            "Session": session_ok,
            "Candle Confirm": candle_bull_ok,
        }
        checklist_bear = {
            "EMA": ema_bear, "ADX": adx_ok, "Supertrend": st_bear,
            "VWAP": vwap_bear, "RSI": rsi_bear, "MACD": macd_bear,
            "MTF": mtf_bear, "ATR": atr_ok,
            "Session": session_ok,
            "Candle Confirm": candle_bear_ok,
        }
        if volume_data_ok:
            checklist_bull["Volume"] = volume_ok
            checklist_bull["Volume Spike"] = volume_spike_ok
            checklist_bear["Volume"] = volume_ok
            checklist_bear["Volume Spike"] = volume_spike_ok
        checklist = checklist_bull if buy_score >= sell_score else checklist_bear
        failed = [k for k, v in checklist.items() if not v]
        reasons = [f"NO TRADE - failed: {', '.join(failed)}"] if failed else ["NO TRADE - score below threshold"]
        if pattern_direction:
            reasons.append(f"{pattern_direction} Pattern (info only): {pattern_name}")
        if bb_signal != "None":
            reasons.append(f"Bollinger (info only): {bb_signal}")

    ai_score = round(buy_score if final_signal == "BUY" else sell_score if final_signal == "SELL" else max(buy_score, sell_score), 2)
    ai_score = min(ai_score, 100)

    def confidence_from_confirmations(confirmations, score):
        """
        Confidence is no longer just a copy of ai_score - it's driven by
        HOW MANY confirmations agreed, per spec:
          9  confirmations -> 70%  (new relaxed tier - Reduced Risk)
          10 confirmations -> 82%
          11 confirmations -> 90%
          12 confirmations -> 98-100% (scaled by score within that band)
        Below 9 (NO TRADE cases) it falls back to a damped score-based
        estimate so it never reaches the 70%+ band without 9+ confirmations.
        """
        if confirmations >= 12:
            bonus = max(0.0, min(1.0, (score - 90) / 10))  # 0..1
            return round(98 + bonus * 2, 1)  # 98 -> 100
        if confirmations == 11:
            return 90.0
        if confirmations == 10:
            return 82.0
        if confirmations == 9:
            return 70.0
        if confirmations == 8:
            return 60.0
        return round(min(55.0, score * 0.7), 1)

    active_confirmations = buy_confirmations if final_signal == "BUY" else \
        sell_confirmations if final_signal == "SELL" else max(buy_confirmations, sell_confirmations)
    confidence = min(confidence_from_confirmations(active_confirmations, ai_score), 100)

    def position_sizing(confirmations):
        """
        Confirmation count -> suggested risk tier. This is what makes the
        relaxed 9-confirmation threshold safe to trade: a 9-confirmation
        signal is clearly flagged as lower quality and paired with a
        smaller suggested size, instead of being treated the same as a
        clean 12/12 setup.
        """
        if confirmations >= 12:
            return "Full Size", "100%"
        if confirmations == 11:
            return "Full Size", "100%"
        if confirmations == 10:
            return "Standard Size", "75%"
        if confirmations == 9:
            return "Reduced Risk - Half Size", "50%"
        if confirmations == 8:
            return "Reduced Risk - Quarter Size", "25%"
        return "Not Traded", "0%"

    signal_tier, position_size_pct = position_sizing(active_confirmations) if final_signal != "NO TRADE" else ("-", "-")

    if ai_score >= 90:
        grade = "A+"
    elif ai_score >= 80:
        grade = "A"
    elif ai_score >= 70:
        grade = "B"
    elif ai_score >= 60:
        grade = "C"
    else:
        grade = "D"

    market_status = "Active" if session_active else "Low Liquidity"

    trade_levels = calculate_trade(
        final_signal, price, atr_value, decimals=decimals, session_active=session_active
    )

    return {
        "signal": final_signal,
        "confidence": confidence,
        "ai_score": ai_score,
        "grade": grade,
        "signal_tier": signal_tier,
        "position_size": position_size_pct,
        "market_status": market_status,
        "session": session_name,
        "session_active": session_active,
        "trend_1m": trend1,
        "trend_5m": trend5,
        "trend_15m": trend15,
        "trend_strength": trend_power,
        "ema_ok": (
            ema_bull if final_signal == "BUY"
            else ema_bear if final_signal == "SELL"
            else (ema_bull or ema_bear)
        ),
        "adx_ok": adx_ok,
        "vwap_ok": vwap_bear if final_signal == "SELL" else vwap_bull,
        "supertrend_ok": st_bear if final_signal == "SELL" else st_bull,
        "volume_ok": volume_ok,
        "atr_ok": atr_ok,
        "liquidity_ok": liquidity_ok,
        "macd": macd_value,
        "rsi": rsi_value,
        "pattern": pattern_name,
        "liquidity_sweep": smc["liquidity_side"] if smc["liquidity"] else "NO",
        "bollinger": bb_signal,
        "buy_confirmations": buy_confirmations,
        "sell_confirmations": sell_confirmations,
        "reasons": reasons,
        "valid_minutes": SIGNAL_VALID_MINUTES,
        "atr_value": atr_value,
        **trade_levels,
    }
