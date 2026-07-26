"""
trade_management.py
Items 16-20 and 68-73: adaptive stop loss, trailing stop, break-even,
partial take-profit, time-based exit, ATR-based TP ladder, RR optimisation.
"""
from dataclasses import dataclass, field


@dataclass
class TradeConfig:
    atr_sl_mult: float = 1.5          # item 16
    atr_trail_mult: float = 1.2       # item 17
    breakeven_at_r: float = 1.0       # item 18 / 68: move SL to BE after +1R
    tp1_r: float = 1.0                # item 70
    tp2_r: float = 2.0
    tp3_r: float = 3.0
    tp1_close_pct: float = 0.4        # item 19: partial take profit
    tp2_close_pct: float = 0.3
    tp3_close_pct: float = 0.3
    max_bars_in_trade: int = 240      # item 20 / 72: time-based exit (240 min on 1m data)
    weak_trade_bars: int = 30         # item 73: if no progress in N bars, close early
    weak_trade_progress_r: float = 0.2


@dataclass
class OpenTrade:
    direction: str            # 'long' or 'short'
    entry: float
    stop_loss: float
    initial_risk: float       # |entry - stop_loss|
    size: float
    entry_bar: int
    remaining_pct: float = 1.0
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    be_moved: bool = False
    realized_r: float = 0.0
    confirmations: list = field(default_factory=list)
    ai_score: float = None


def r_multiple(trade: OpenTrade, price: float) -> float:
    diff = (price - trade.entry) if trade.direction == "long" else (trade.entry - price)
    return diff / trade.initial_risk if trade.initial_risk else 0.0


def update_trade(trade: OpenTrade, bar, bar_index: int, atr_value: float, cfg: TradeConfig):
    """
    Advances one open trade by one bar. Mutates `trade` in place.
    Returns (closed: bool, reason: str, exit_price: float or None, realized_r_delta: float)
    """
    high, low, close = bar["high"], bar["low"], bar["close"]
    sign = 1 if trade.direction == "long" else -1
    worst_price = low if trade.direction == "long" else high
    best_price = high if trade.direction == "long" else low

    # --- Stop loss / trailing stop (17) ---
    hit_sl = (worst_price <= trade.stop_loss) if trade.direction == "long" else (worst_price >= trade.stop_loss)
    if hit_sl:
        r = r_multiple(trade, trade.stop_loss)
        return True, "stop_loss", trade.stop_loss, r * trade.remaining_pct + trade.realized_r

    cur_r = r_multiple(trade, best_price)

    # --- Break-even (18/68) ---
    if not trade.be_moved and cur_r >= cfg.breakeven_at_r:
        trade.stop_loss = trade.entry
        trade.be_moved = True

    # --- ATR trailing stop once in profit past BE (17) ---
    if trade.be_moved:
        trail = close - sign * atr_value * cfg.atr_trail_mult
        if trade.direction == "long":
            trade.stop_loss = max(trade.stop_loss, trail)
        else:
            trade.stop_loss = min(trade.stop_loss, trail)

    # --- Partial take-profit ladder (19/70) ---
    realized_delta = 0.0
    if not trade.tp1_hit and cur_r >= cfg.tp1_r:
        realized_delta += cfg.tp1_r * cfg.tp1_close_pct
        trade.remaining_pct -= cfg.tp1_close_pct
        trade.tp1_hit = True
    if not trade.tp2_hit and cur_r >= cfg.tp2_r:
        realized_delta += cfg.tp2_r * cfg.tp2_close_pct
        trade.remaining_pct -= cfg.tp2_close_pct
        trade.tp2_hit = True
    if not trade.tp3_hit and cur_r >= cfg.tp3_r:
        realized_delta += cfg.tp3_r * cfg.tp3_close_pct
        trade.remaining_pct -= cfg.tp3_close_pct
        trade.tp3_hit = True
        trade.realized_r += realized_delta
        return True, "tp3_full_close", close, trade.realized_r

    trade.realized_r += realized_delta

    # --- Time-based exit (20/72) ---
    bars_open = bar_index - trade.entry_bar
    if bars_open >= cfg.max_bars_in_trade:
        r = r_multiple(trade, close)
        return True, "time_exit", close, r * trade.remaining_pct + trade.realized_r

    # --- Auto-close weak trades (73) ---
    if bars_open >= cfg.weak_trade_bars and cur_r < cfg.weak_trade_progress_r and not trade.be_moved:
        r = r_multiple(trade, close)
        return True, "weak_trade_close", close, r * trade.remaining_pct + trade.realized_r

    return False, None, None, 0.0
