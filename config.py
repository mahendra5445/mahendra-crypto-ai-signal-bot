import os

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Telegram channel where signals are posted (public channel username,
# e.g. "@mscryptoaisignals", OR a private channel's numeric chat id
# e.g. "-1001234567890"). The bot must be an admin of this channel with
# "Post Messages" permission.
CHANNEL_ID = os.getenv("CHANNEL_ID")

# ==========================================================================
# ASSET REGISTRY
# ==========================================================================
# 12 high-liquidity crypto pairs. `symbol` is the Yahoo Finance ticker
# (primary data source), `binance` is the Binance ticker used as an
# external fallback/sanity-check price source when Yahoo fails or glitches.
# `decimals` controls display/rounding precision for that asset's price
# and signal levels — small-price coins need more decimals or SL/TP
# levels round incorrectly.
ASSETS = {
    "btc": {
        "symbol":   "BTC-USD",
        "fallback": None,
        "binance":  "BTCUSDT",
        "decimals": 2,
        "label":    "BTC",
    },
    "eth": {
        "symbol":   "ETH-USD",
        "fallback": None,
        "binance":  "ETHUSDT",
        "decimals": 2,
        "label":    "ETH",
    },
    "sol": {
        "symbol":   "SOL-USD",
        "fallback": None,
        "binance":  "SOLUSDT",
        "decimals": 2,
        "label":    "SOL",
    },
    "xrp": {
        "symbol":   "XRP-USD",
        "fallback": None,
        "binance":  "XRPUSDT",
        "decimals": 4,
        "label":    "XRP",
    },
    "bnb": {
        "symbol":   "BNB-USD",
        "fallback": None,
        "binance":  "BNBUSDT",
        "decimals": 2,
        "label":    "BNB",
    },
    "doge": {
        "symbol":   "DOGE-USD",
        "fallback": None,
        "binance":  "DOGEUSDT",
        "decimals": 5,
        "label":    "DOGE",
    },
    "ada": {
        "symbol":   "ADA-USD",
        "fallback": None,
        "binance":  "ADAUSDT",
        "decimals": 4,
        "label":    "ADA",
    },
    "link": {
        "symbol":   "LINK-USD",
        "fallback": None,
        "binance":  "LINKUSDT",
        "decimals": 4,
        "label":    "LINK",
    },
    "avax": {
        "symbol":   "AVAX-USD",
        "fallback": None,
        "binance":  "AVAXUSDT",
        "decimals": 2,
        "label":    "AVAX",
    },
    "ton": {
        "symbol":   "TON-USD",
        "fallback": None,
        "binance":  "TONUSDT",
        "decimals": 3,
        "label":    "TON",
    },
    "sui": {
        "symbol":   "SUI-USD",
        "fallback": None,
        "binance":  "SUIUSDT",
        "decimals": 4,
        "label":    "SUI",
    },
    "ltc": {
        "symbol":   "LTC-USD",
        "fallback": None,
        "binance":  "LTCUSDT",
        "decimals": 2,
        "label":    "LTC",
    },
}

ASSET_LIST = list(ASSETS.keys())

INTERVAL = "1min"
