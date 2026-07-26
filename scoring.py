"""
scoring.py
Item 15: Dynamic AI Score System, plus items 51-56 (signal quality) and
80-85 (self-learning weight adjustment).

Design: every confirmation module contributes a signed vote (+1 bullish,
-1 bearish, 0 neutral) times a learned weight. The weighted sum is
squashed to a 0-100 "AI score" plus a direction.
"""
import json
import os
import numpy as np
import pandas as pd

DEFAULT_WEIGHTS = {
    "trend_ema": 1.0,
    "structure": 1.2,
    "order_block": 1.0,
    "fvg": 0.8,
    "premium_discount": 0.7,
    "liquidity_sweep": 1.3,
    "smart_money_entry": 1.5,
    "momentum_candle": 0.6,
    "rsi": 0.6,
    "macd": 0.6,
    "adx": 0.5,
    "vwap": 0.4,
    "volume_spike": 0.5,
    "rvol": 0.4,
    "mtf_confirmation": 1.4,
    "session_quality": 0.3,
    "equal_high_low": 0.6,
    "inducement": 0.7,
    "breaker_block": 0.8,
    "mitigation_block": 0.7,
    "fake_breakout": 0.9,
    "institutional_zone": 0.5,
    "cvd_divergence": 0.8,
}


class WeightStore:
    """Item 83/85: persisted, adaptive weights (self-learning)."""

    def __init__(self, path: str):
        self.path = path
        if os.path.exists(path):
            with open(path) as f:
                self.weights = json.load(f)
        else:
            self.weights = dict(DEFAULT_WEIGHTS)
            self.save()

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.weights, f, indent=2)

    def update_from_trades(self, trades: pd.DataFrame, lr: float = 0.05):
        """
        Items 80-82, 84: bump weights of confirmations present in winning
        trades, decay weights of confirmations present in losing trades.
        `trades` must have a 'confirmations' column: list[str], and 'pnl'.
        """
        if trades.empty:
            return
        for _, row in trades.iterrows():
            direction = 1 if row["pnl_r"] > 0 else -1
            for conf in row.get("confirmations", []) or []:
                if conf in self.weights:
                    self.weights[conf] = max(
                        0.05, min(3.0, self.weights[conf] + lr * direction)
                    )
        self.save()


def compute_ai_score(votes: dict, weights: dict) -> dict:
    """
    votes: {confirmation_name: +1 / -1 / 0}
    Returns dict with numeric score 0-100, direction, and confidence.
    """
    total_weight = sum(weights.get(k, 0.5) for k in votes)
    weighted_sum = sum(votes[k] * weights.get(k, 0.5) for k in votes)
    if total_weight == 0:
        return {"score": 50, "direction": "neutral", "confidence": 0.0, "confirmations": []}
    normalized = weighted_sum / total_weight  # -1..+1
    score = 50 + normalized * 50
    direction = "long" if normalized > 0.15 else "short" if normalized < -0.15 else "neutral"
    active = [k for k, v in votes.items() if v != 0]
    return {
        "score": round(float(np.clip(score, 0, 100)), 2),
        "direction": direction,
        "confidence": round(abs(normalized), 3),
        "confirmations": active,
    }


def minimum_confirmation_score(result: dict, min_score: float = 65.0) -> bool:
    """Item 51."""
    return result["score"] >= min_score or result["score"] <= (100 - min_score)


def signal_expiry_bars(atr_z: float, base_bars: int = 5) -> int:
    """Item 53: higher volatility -> signal expires faster (fewer bars valid)."""
    factor = 1.0 / (1.0 + max(atr_z, 0))
    return max(1, int(round(base_bars * factor)))


def trend_probability(score: float) -> float:
    """Item 56: maps AI score to an intuitive 0-100% trend-continuation probability."""
    return round(abs(score - 50) * 2, 2)
