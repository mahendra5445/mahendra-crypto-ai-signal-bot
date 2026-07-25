"""
Central configuration.

Everything you might want to tune without editing code is read from an
environment variable with a sane default, so Railway's Variables tab is
enough to retune the bot.
"""

import math
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Telegram channel where signals are posted (public channel username,
# e.g. "@mscryptoaisignals", OR a private channel's numeric chat id
# e.g. "-1001234567890"). The bot must be an admin of that channel with
# "Post Messages" permission.
CHANNEL_ID = os.getenv("CHANNEL_ID")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# ==========================================================================
# TIMING
# ==========================================================================
SIGNAL_CYCLE_SEC     = _int_env("SIGNAL_CYCLE_SEC", 900)    # full asset scan interval
SIGNAL_COOLDOWN_SEC  = _int_env("SIGNAL_COOLDOWN_SEC", 900) # per-asset silence after a signal
MONITOR_INTERVAL_SEC = _int_env("MONITOR_INTERVAL_SEC", 60) # SL/TP poll interval

# Warm-up before the FIRST scan. Stops the bot from dumping a burst of
# signals the instant the process boots — the moment data is most likely to
# be stale straight after a redeploy, and (on ephemeral storage) the moment
# the guards have zero history to throttle against. Set 0 to scan on boot.
STARTUP_DELAY_SEC = _int_env("STARTUP_DELAY_SEC", 60)

# Hard cap on how many NEW trades a single scan cycle may open. The 12 coins
# are highly correlated, so a clean trend makes several of them qualify in
# the same scan — without this cap that becomes 3-4 near-identical entries in
# one minute (this is exactly the "deploy ke 1 min baad 3 trade" behaviour).
# Capping per cycle spaces entries out across cycles instead.
MAX_NEW_TRADES_PER_CYCLE = _int_env("MAX_NEW_TRADES_PER_CYCLE", 2)

# ==========================================================================
# SIGNAL GATE  (used by strategy.py)
# ==========================================================================
# How many of the 12 confirmations must agree before a signal is posted and
# tracked, and the minimum weighted score (0-100). Kept here so both are
# tunable from Railway's Variables tab without editing code.
#   MIN_CONFIRMATIONS 9  → drops the weakest "Quarter Size" (8/12) tier that
#                          used to auto-post as a real trade. Lower to 8 only
#                          if you deliberately want more, lower-quality fills.
MIN_CONFIRMATIONS = _int_env("MIN_CONFIRMATIONS", 9)
MIN_SCORE         = _int_env("MIN_SCORE", 62)

# ==========================================================================
# RISK GUARDS  (enforced in guards.py)
# ==========================================================================
MAX_OPEN_TRADES    = _int_env("MAX_OPEN_TRADES", 4)
MAX_TRADES_PER_DAY = _int_env("MAX_TRADES_PER_DAY", 12)
MAX_CONSEC_LOSSES  = _int_env("MAX_CONSEC_LOSSES", 4)
LOSS_PAUSE_SEC     = _int_env("LOSS_PAUSE_SEC", 6 * 3600)

# ==========================================================================
# ASSET REGISTRY
# ==========================================================================
# `symbol`   — Yahoo Finance ticker (primary data source)
# `binance`  — Binance ticker, used as an independent fallback price source
# `decimals` — MINIMUM display precision. effective_decimals() below raises
#              it automatically when the coin's price is low enough that this
#              rounding would distort the SL/TP distances.
ASSETS = {
    "btc":  {"symbol": "BTC-USD",  "fallback": None, "binance": "BTCUSDT",  "decimals": 2, "label": "BTC"},
    "eth":  {"symbol": "ETH-USD",  "fallback": None, "binance": "ETHUSDT",  "decimals": 2, "label": "ETH"},
    "sol":  {"symbol": "SOL-USD",  "fallback": None, "binance": "SOLUSDT",  "decimals": 2, "label": "SOL"},
    "xrp":  {"symbol": "XRP-USD",  "fallback": None, "binance": "XRPUSDT",  "decimals": 4, "label": "XRP"},
    "bnb":  {"symbol": "BNB-USD",  "fallback": None, "binance": "BNBUSDT",  "decimals": 2, "label": "BNB"},
    "doge": {"symbol": "DOGE-USD", "fallback": None, "binance": "DOGEUSDT", "decimals": 5, "label": "DOGE"},
    "ada":  {"symbol": "ADA-USD",  "fallback": None, "binance": "ADAUSDT",  "decimals": 4, "label": "ADA"},
    "link": {"symbol": "LINK-USD", "fallback": None, "binance": "LINKUSDT", "decimals": 4, "label": "LINK"},
    "avax": {"symbol": "AVAX-USD", "fallback": None, "binance": "AVAXUSDT", "decimals": 2, "label": "AVAX"},
    "ton":  {"symbol": "TON-USD",  "fallback": None, "binance": "TONUSDT",  "decimals": 3, "label": "TON"},
    "sui":  {"symbol": "SUI-USD",  "fallback": None, "binance": "SUIUSDT",  "decimals": 4, "label": "SUI"},
    "ltc":  {"symbol": "LTC-USD",  "fallback": None, "binance": "LTCUSDT",  "decimals": 2, "label": "LTC"},
}

ASSET_LIST = list(ASSETS.keys())


# ==========================================================================
# PRICE-AWARE ROUNDING
# ==========================================================================
# BUG FIX (the "AVAX class" of bug): AVAX was configured with decimals=2.
# At a price of $6.26 the minimum stop distance (0.15% = $0.0094) rounds to
# exactly ONE cent — the whole stop is one tick wide. Every SL/TP level then
# snaps onto the same cent grid, so a "1:1.2" risk-reward posted to the
# channel was really 1:1.0, and every R-multiple in the stats was wrong by
# 20-25%.
#
# Fix: derive precision from the actual price instead of trusting a
# hardcoded per-coin number. We require the minimum stop to span at least
# MIN_TICKS_PER_STOP ticks. The configured `decimals` is a floor (never show
# fewer decimals than configured); 8 is the ceiling.

MIN_TICKS_PER_STOP = 50
MIN_RISK_PCT       = 0.0015   # also used by risk.py as the stop-distance floor


def effective_decimals(asset: str, price: float | None) -> int:
    """
    Decimal precision that is actually safe for this asset at this price.

    Returns the configured `decimals` when it is already fine (BTC, ETH) and
    raises it for low-priced coins (AVAX at $6, DOGE, SUI, ...).
    """
    cfg  = ASSETS.get(str(asset).lower(), {})
    base = int(cfg.get("decimals", 2))

    try:
        price = float(price)
    except (TypeError, ValueError):
        return base
    if price <= 0:
        return base

    tick_needed = price * MIN_RISK_PCT / MIN_TICKS_PER_STOP
    needed      = math.ceil(-math.log10(tick_needed))

    return max(base, min(needed, 8))
