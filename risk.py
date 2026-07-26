"""
risk.py
Items 44-50: risk management.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class RiskConfig:
    account_balance: float = 1000.0
    risk_per_trade_pct: float = 1.0       # item 45
    max_daily_loss_pct: float = 3.0       # item 46
    max_consecutive_losses: int = 3       # item 47
    max_open_trades: int = 1              # item 48
    cooldown_minutes: int = 15            # item 49


@dataclass
class RiskState:
    daily_pnl_pct: float = 0.0
    consecutive_losses: int = 0
    open_trades: int = 0
    last_trade_time: datetime = None
    last_signal_key: str = None           # item 50: duplicate signal protection
    current_day: str = None


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.state = RiskState()

    def _roll_day(self, ts: datetime):
        day = ts.strftime("%Y-%m-%d")
        if self.state.current_day != day:
            self.state.current_day = day
            self.state.daily_pnl_pct = 0.0
            self.state.consecutive_losses = 0  # reset daily; adjust if you want it persistent

    def position_size(self, entry: float, stop_loss: float) -> float:
        """Item 44/45: Dynamic, risk-based position size (in base asset units)."""
        risk_amount = self.cfg.account_balance * (self.cfg.risk_per_trade_pct / 100)
        stop_distance = abs(entry - stop_loss)
        if stop_distance <= 0:
            return 0.0
        return round(risk_amount / stop_distance, 6)

    def can_trade(self, ts: datetime, signal_key: str) -> tuple[bool, str]:
        self._roll_day(ts)
        if self.state.daily_pnl_pct <= -abs(self.cfg.max_daily_loss_pct):
            return False, "max_daily_loss_hit"
        if self.state.consecutive_losses >= self.cfg.max_consecutive_losses:
            return False, "max_consecutive_losses_hit"
        if self.state.open_trades >= self.cfg.max_open_trades:
            return False, "max_open_trades_reached"
        if self.state.last_trade_time is not None:
            if ts - self.state.last_trade_time < timedelta(minutes=self.cfg.cooldown_minutes):
                return False, "cooldown_active"
        if signal_key == self.state.last_signal_key:
            return False, "duplicate_signal"
        return True, "ok"

    def register_trade_open(self, ts: datetime, signal_key: str):
        self.state.open_trades += 1
        self.state.last_trade_time = ts
        self.state.last_signal_key = signal_key

    def register_trade_close(self, pnl_pct: float):
        self.state.open_trades = max(0, self.state.open_trades - 1)
        self.state.daily_pnl_pct += pnl_pct
        if pnl_pct < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0
