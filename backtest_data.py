"""
Historical 1-minute data downloader (Binance public API, no key needed).

WHY NOT YAHOO: Yahoo only serves about 7 days of 1-minute candles. A
backtest over 7 days of one market regime tells you nothing you can trust.
Binance serves years of 1m klines for every pair this bot trades, and the
bot's config already carries the Binance ticker for each coin.

Everything is cached to disk, so a re-run costs nothing. Downloading 90
days of 1m data for 12 coins is roughly 130k candles per coin — expect a
few minutes on the first run.
"""

from __future__ import annotations

import csv
import logging
import os
import time
from datetime import datetime, timezone

import requests

from config import ASSETS


def _coinbase_product(binance_symbol: str) -> str:
    """BTCUSDT -> BTC-USD, the product id Coinbase and config.ASSETS both use."""
    for asset, cfg in ASSETS.items():
        if cfg["binance"] == binance_symbol:
            return cfg["symbol"]
    return binance_symbol.replace("USDT", "-USD")

logger = logging.getLogger(__name__)

# Tried in order. This matters more than it looks:
#   - data-api.binance.vision is the read-only public mirror
#   - api.binance.com is the main host, and it returns HTTP 451 to any US IP
#   - api.binance.us is the separate US entity and DOES answer from US IPs,
#     which is what makes this work on Google Colab / Kaggle (their servers
#     are US-based Google Cloud machines that Binance blocks)
# If every Binance host fails, we fall back to Coinbase below.
HOSTS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api.binance.us",
]

COINBASE = "https://api.exchange.coinbase.com"

CACHE_DIR = os.getenv("BACKTEST_DATA_DIR", "backtest_data")
LIMIT = 1000          # max candles per request
COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def _cache_path(symbol: str) -> str:
    return os.path.join(CACHE_DIR, f"{symbol}_1m.csv")


def _read_cache(symbol: str) -> list[dict]:
    path = _cache_path(symbol)
    if not os.path.exists(path):
        return []
    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "ts":     int(r["ts"]),
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": float(r["volume"]),
            })
    return rows


def _write_cache(symbol: str, rows: list[dict]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(symbol), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


_working_host: str | None = None


def _get(path: str, params: dict) -> list:
    """Query the first Binance host that answers, and remember which one."""
    global _working_host

    hosts = [_working_host] + [h for h in HOSTS if h != _working_host] \
        if _working_host else HOSTS

    last_err = None
    for host in hosts:
        try:
            r = requests.get(host + path, params=params, timeout=20)
            if r.status_code == 429:
                logger.warning("[DATA] rate limited — sleeping 30s")
                time.sleep(30)
                r = requests.get(host + path, params=params, timeout=20)
            if r.status_code == 451:
                logger.warning(f"[DATA] {host} → 451 geo-blocked, trying next host")
                last_err = RuntimeError("451 geo-blocked")
                continue
            r.raise_for_status()
            _working_host = host
            return r.json()
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"all Binance hosts failed: {last_err}")


def check_connectivity() -> str:
    """
    Which data source is reachable from here? Call this FIRST — it turns a
    ten-minute failed download into a two-second answer.
    """
    for host in HOSTS:
        try:
            r = requests.get(host + "/api/v3/klines",
                             {"symbol": "BTCUSDT", "interval": "1m", "limit": 1},
                             timeout=10)
            if r.ok:
                return host
        except Exception:
            continue
    try:
        r = requests.get(f"{COINBASE}/products/BTC-USD/candles",
                         {"granularity": 60}, timeout=10)
        if r.ok:
            return COINBASE
    except Exception:
        pass
    return ""


# ── Coinbase fallback ─────────────────────────────────────────────────────
#
# Used when every Binance host is blocked. Slower — Coinbase caps a request
# at 300 candles instead of 1000 — but it is reachable from US cloud IPs,
# and the bot's config already stores each coin in Coinbase's "BTC-USD"
# product format.

def _fetch_coinbase(product: str, start_ms: int, end_ms: int) -> list[dict]:
    rows: list[dict] = []
    span = 300 * 60          # seconds covered by one request
    cursor = start_ms // 1000

    while cursor < end_ms // 1000:
        stop = min(cursor + span, end_ms // 1000)
        try:
            r = requests.get(
                f"{COINBASE}/products/{product}/candles",
                params={
                    "granularity": 60,
                    "start": datetime.fromtimestamp(cursor, tz=timezone.utc).isoformat(),
                    "end":   datetime.fromtimestamp(stop, tz=timezone.utc).isoformat(),
                },
                timeout=20,
            )
            if r.status_code == 429:
                time.sleep(1.0)
                continue
            r.raise_for_status()
            # Coinbase returns [time, low, high, open, close, volume], newest first
            for c in r.json():
                rows.append({
                    "ts":     int(c[0]) * 1000,
                    "open":   float(c[3]),
                    "high":   float(c[2]),
                    "low":    float(c[1]),
                    "close":  float(c[4]),
                    "volume": float(c[5]),
                })
        except Exception as e:
            logger.warning(f"[DATA] Coinbase chunk failed: {e}")
        cursor = stop
        time.sleep(0.22)     # Coinbase public rate limit is ~10 req/s

    rows.sort(key=lambda b: b["ts"])
    return rows


def fetch_1m(symbol: str, days: int) -> list[dict]:
    """
    `days` of 1-minute candles for a Binance symbol, oldest first.
    Uses and extends the on-disk cache.
    """
    end_ms   = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 60 * 60 * 1000

    cached = _read_cache(symbol)
    if cached:
        have_from, have_to = cached[0]["ts"], cached[-1]["ts"]
        if have_from <= start_ms and have_to >= end_ms - 5 * 60 * 1000:
            logger.info(f"[DATA] {symbol}: cache hit ({len(cached)} candles)")
            return [c for c in cached if c["ts"] >= start_ms]
        # extend forward from what we already have
        if have_from <= start_ms:
            start_ms = have_to + 60_000

    rows: list[dict] = []
    cursor = start_ms
    binance_dead = False

    while cursor < end_ms:
        try:
            batch = _get("/api/v3/klines", {
                "symbol": symbol, "interval": "1m",
                "startTime": cursor, "limit": LIMIT,
            })
        except RuntimeError as e:
            logger.warning(f"[DATA] Binance unavailable ({e}) — falling back to Coinbase")
            binance_dead = True
            break
        if not batch:
            break
        for k in batch:
            rows.append({
                "ts":     int(k[0]),
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
            })
        cursor = int(batch[-1][0]) + 60_000
        if len(batch) < LIMIT:
            break
        time.sleep(0.12)   # stay well inside Binance's rate limit

    if binance_dead:
        product = _coinbase_product(symbol)
        logger.info(f"[DATA] {symbol}: downloading from Coinbase as {product} (slower)")
        rows = _fetch_coinbase(product, start_ms, end_ms)

    merged = {c["ts"]: c for c in cached}
    merged.update({c["ts"]: c for c in rows})
    out = [merged[t] for t in sorted(merged)]
    _write_cache(symbol, out)
    logger.info(f"[DATA] {symbol}: {len(out)} candles cached")

    return [c for c in out if c["ts"] >= end_ms - days * 24 * 60 * 60 * 1000]


def fetch_asset(asset: str, days: int) -> list[dict]:
    cfg = ASSETS[asset.lower()]
    return fetch_1m(cfg["binance"], days)
