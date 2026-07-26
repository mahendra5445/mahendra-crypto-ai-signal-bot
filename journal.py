"""
journal.py
Items 74-79 (performance) and 88 (persistent database), 89 (duplicate
trade protection at the storage layer).
"""
import json
import os
import sqlite3
from datetime import datetime

import pandas as pd


SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_key TEXT UNIQUE,
    entry_time TEXT,
    exit_time TEXT,
    direction TEXT,
    entry_price REAL,
    exit_price REAL,
    stop_loss REAL,
    size REAL,
    pnl_r REAL,
    pnl_pct REAL,
    exit_reason TEXT,
    confirmations TEXT,
    ai_score REAL
);
"""


class TradeJournal:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def log_trade(self, trade: dict):
        """Item 89: signal_key is UNIQUE -> duplicate inserts are ignored, not crashed."""
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO trades
                   (signal_key, entry_time, exit_time, direction, entry_price, exit_price,
                    stop_loss, size, pnl_r, pnl_pct, exit_reason, confirmations, ai_score)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trade["signal_key"], trade["entry_time"], trade["exit_time"], trade["direction"],
                    trade["entry_price"], trade["exit_price"], trade["stop_loss"], trade["size"],
                    trade["pnl_r"], trade["pnl_pct"], trade["exit_reason"],
                    json.dumps(trade.get("confirmations", [])), trade.get("ai_score"),
                ),
            )
            self.conn.commit()
        except sqlite3.Error as e:
            # Item 92/93: error logging + exception handling instead of a hard crash
            print(f"[journal] failed to log trade {trade.get('signal_key')}: {e}")

    def to_dataframe(self) -> pd.DataFrame:
        df = pd.read_sql("SELECT * FROM trades ORDER BY id", self.conn)
        if not df.empty:
            df["confirmations"] = df["confirmations"].apply(json.loads)
        return df

    def export_csv(self, path: str):
        self.to_dataframe().to_csv(path, index=False)


def performance_report(trades: pd.DataFrame) -> dict:
    """Items 75-78: win rate, profit factor, drawdown."""
    if trades.empty:
        return {"trades": 0}
    wins = trades[trades["pnl_r"] > 0]
    losses = trades[trades["pnl_r"] <= 0]
    win_rate = len(wins) / len(trades) * 100
    gross_win = wins["pnl_r"].sum()
    gross_loss = abs(losses["pnl_r"].sum())
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    equity = trades["pnl_r"].cumsum()
    running_max = equity.cummax()
    drawdown = equity - running_max
    max_dd = drawdown.min()

    return {
        "trades": len(trades),
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
        "total_r": round(equity.iloc[-1], 2),
        "max_drawdown_r": round(max_dd, 2),
        "avg_win_r": round(wins["pnl_r"].mean(), 3) if len(wins) else 0,
        "avg_loss_r": round(losses["pnl_r"].mean(), 3) if len(losses) else 0,
    }


def monthly_report(trades: pd.DataFrame) -> pd.DataFrame:
    """Item 78: monthly performance report."""
    if trades.empty:
        return pd.DataFrame()
    t = trades.copy()
    t["month"] = pd.to_datetime(t["exit_time"]).dt.to_period("M")
    return t.groupby("month").agg(
        trades=("pnl_r", "count"),
        total_r=("pnl_r", "sum"),
        win_rate_pct=("pnl_r", lambda x: (x > 0).mean() * 100),
    )
