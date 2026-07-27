"""
Data fetching from Yahoo Finance.

Fixes applied:
  #8  API retry + exponential backoff — _fetch_tf wraps a single-attempt
                                        helper with up to 3 retries.
  #9  API timeout                     — explicit 15-s timeout on every request.
 #11  Stale candle validation         — last candle timestamp checked; warning
                                        logged if data is older than MAX_STALE_H.
  MT5 price fix                       — spot ticker tried first; futures
                                        fallback used only when configured
                                        (gold).

  Multi-asset refactor                — gold/BTC ke hardcoded branches hata
                                        kar config.ASSETS registry se generic
                                        bana diya gaya, taaki Oil, EUR/USD,
                                        USD/JPY, LINK, ATOM sab isi code path
                                        se guzrein bina duplication ke.
"""

import logging
import os
import time as _time

import requests

from config import ASSETS

logger = logging.getLogger(__name__)

YF_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

_YF_INTERVAL = {"1min": "1m", "5min": "5m", "15min": "15m"}
_YF_RANGE    = {"1min": "5d", "5min": "5d", "15min": "5d"}

# Hard-fail when the newest closed candle is older than this (C-002).
# Crypto trades 24/7 — 30 minutes is already far beyond a usable 5m signal.
MAX_STALE_H = float(os.getenv("MAX_STALE_H", "0.5"))

# Retry settings
MAX_RETRIES      = 3
RETRY_BASE_DELAY = 1.0   # seconds; actual delays: 1 s, 2 s (2^0, 2^1)


def _asset_cfg(asset: str) -> dict:
    a = asset.lower()
    if a not in ASSETS:
        raise ValueError(f"Unknown asset '{asset}'. Known assets: {list(ASSETS)}")
    return ASSETS[a]


class _YahooGlitch(Exception):
    """Yahoo ka live quote uske apne recent data se itna alag hai ki
    feed pe bharosa nahi kiya ja sakta — external source use karo."""


# ── single-attempt fetch (no retry) ──────────────────────────────────────

def _fetch_tf_once(symbol: str, interval_key: str, label: str) -> dict:
    yf_interval = _YF_INTERVAL[interval_key]
    yf_range    = _YF_RANGE[interval_key]
    url         = YF_URL.format(symbol=symbol)
    params      = {"interval": yf_interval, "range": yf_range}

    try:
        r = requests.get(url, params=params, headers=YF_HEADERS, timeout=15)
        r.raise_for_status()
        payload = r.json()
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(
            f"[{label} {interval_key}] HTTP {e.response.status_code}: "
            f"{e.response.text[:300]}"
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"[{label} {interval_key}] Network/timeout error: {e}")
    except ValueError as e:
        raise RuntimeError(f"[{label} {interval_key}] Bad JSON: {e}")

    chart = payload.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"[{label} {interval_key}] Yahoo Finance error: {chart['error']}")

    result = chart.get("result")
    if not result:
        raise RuntimeError(f"[{label} {interval_key}] No data returned.")

    r0         = result[0]
    timestamps = r0.get("timestamp") or []
    quote      = (r0.get("indicators", {}).get("quote") or [{}])[0]

    opens   = quote.get("open")   or []
    highs   = quote.get("high")   or []
    lows    = quote.get("low")    or []
    closes  = quote.get("close")  or []
    volumes = quote.get("volume") or []

    rows = []
    for i in range(len(timestamps)):
        o = opens[i]   if i < len(opens)   else None
        h = highs[i]   if i < len(highs)   else None
        l = lows[i]    if i < len(lows)    else None
        c = closes[i]  if i < len(closes)  else None
        if None in (o, h, l, c):
            continue
        v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0
        ts = timestamps[i] if i < len(timestamps) else None
        rows.append((o, h, l, c, v, ts))

    if not rows:
        raise RuntimeError(f"[{label} {interval_key}] No usable candles in response.")

    rows = rows[:-1]   # drop the still-forming candle

    if len(rows) < 200:
        raise RuntimeError(
            f"[{label} {interval_key}] Only {len(rows)} closed candles "
            f"(need ≥200). Yahoo can have gaps — retry shortly."
        )

    opens_f   = [r[0] for r in rows]
    highs_f   = [r[1] for r in rows]
    lows_f    = [r[2] for r in rows]
    closes_f  = [r[3] for r in rows]
    volumes_f = [r[4] for r in rows]
    last_ts   = rows[-1][5]   # unix timestamp of the last closed candle

    # ── Stale-data hard fail (C-002) ──────────────────────────────────────
    if last_ts is not None:
        age_h = (_time.time() - last_ts) / 3600
        if age_h > MAX_STALE_H:
            raise RuntimeError(
                f"[{label} {interval_key}] STALE DATA: last candle "
                f"{age_h:.1f}h old (max {MAX_STALE_H}h)"
            )

    # H-017: zero volume → explicit missing flag for strategy (do not auto-pass).
    volume_missing = sum(volumes_f) == 0
    if volume_missing:
        volumes_f = None

    return {
        "open":    opens_f,
        "close":   closes_f,
        "high":    highs_f,
        "low":     lows_f,
        "volume":  volumes_f,
        "volume_missing": volume_missing,
        "price":   closes_f[-1],
        "last_ts": last_ts,
    }


# ── fetch with retry + exponential backoff ────────────────────────────────

def _fetch_tf(symbol: str, interval_key: str, label: str) -> dict:
    """
    Up to MAX_RETRIES attempts with exponential back-off (1 s, 2 s).
    Uses time.sleep() — acceptable because the bot only fetches data
    every 15 minutes and a brief event-loop pause is far better than
    a missing candle.
    """
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return _fetch_tf_once(symbol, interval_key, label)
        except RuntimeError as e:
            last_err = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)   # 1 s, 2 s
                logger.warning(
                    f"[RETRY] {label} {interval_key} attempt {attempt + 1}/"
                    f"{MAX_RETRIES} failed — retrying in {delay:.0f}s. {e}"
                )
                _time.sleep(delay)

    raise last_err   # type: ignore[misc]


# ── public helpers ────────────────────────────────────────────────────────

def get_asset_tf(asset: str, interval: str) -> dict:
    """
    Generic per-asset timeframe fetch. Tries the primary symbol first;
    falls back to `fallback` (Yahoo ticker) if configured and primary fails
    (only gold has one today — futures ~$10-25 premium vs spot).
    """
    cfg   = _asset_cfg(asset)
    label = cfg["label"]

    try:
        return _fetch_tf(cfg["symbol"], interval, label)
    except RuntimeError as primary_err:
        if not cfg.get("fallback"):
            raise
        logger.warning(
            f"[{label}] Primary ticker failed ({primary_err}); "
            f"falling back to {cfg['fallback']}"
        )
        data = _fetch_tf(cfg["fallback"], interval, f"{label}(Fallback)")
        logger.warning(f"[{label}] Using fallback ticker — price may differ from primary.")
        return data


# Backward-compatible wrapper (older code imports this name directly)
def get_btc_tf(interval: str) -> dict:
    return get_asset_tf("btc", interval)


# ── external fallback price sources (jab Yahoo fail/glitch kare) ─────────
#
# All 12 assets are crypto, so every one gets the same two independent
# fallback sources:
#   1) Binance   — sabse liquid exchange, no API key needed.
#   2) Coinbase  — backup; product id matches the same "XXX-USD" format
#                  as the Yahoo symbol, so it works generically for any
#                  coin in the registry without asset-specific branches.

def _binance_price(binance_symbol: str) -> float | None:
    r = requests.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": binance_symbol}, timeout=10,
    )
    r.raise_for_status()
    price = r.json().get("price")
    return float(price) if price else None


def _coinbase_price(product_id: str) -> float | None:
    r = requests.get(f"https://api.coinbase.com/v2/prices/{product_id}/spot", timeout=10)
    r.raise_for_status()
    amount = r.json().get("data", {}).get("amount")
    return float(amount) if amount else None


def get_external_price(asset: str) -> float | None:
    """
    Yahoo ke alawa independent sources se live price.
    Sources order mein try hote hain; jo pehla kaam kare wahi return.
    """
    cfg = _asset_cfg(asset)

    sources = []
    if cfg.get("binance"):
        sources.append(("Binance", lambda: _binance_price(cfg["binance"])))
    sources.append(("Coinbase", lambda: _coinbase_price(cfg["symbol"])))

    for name, fn in sources:
        try:
            price = fn()
            if price is not None and price > 0:
                logger.info(f"[PRICE] {asset.upper()} from {name}: {price}")
                return price
        except Exception as e:
            logger.warning(f"[PRICE] {name} failed for {asset}: {e}")
    return None


def get_latest_price(asset: str = "btc") -> float | None:
    """
    Lightweight price check for the trade monitor / manual commands.
    Returns None on failure (caller skips and retries next cycle).
    """
    cfg      = _asset_cfg(asset)
    symbol   = cfg["symbol"]
    fallback = cfg.get("fallback")

    def _single(sym: str) -> float | None:
        url    = YF_URL.format(symbol=sym)
        params = {"interval": "1m", "range": "1d"}
        r = requests.get(url, params=params, headers=YF_HEADERS, timeout=15)
        r.raise_for_status()
        payload = r.json()
        result0 = payload["chart"]["result"][0]
        closes  = (
            result0["indicators"]["quote"][0]
            .get("close") or []
        )
        # Median of the last few valid 1-minute closes — filters out a
        # single bad/stale tick that could fake-trigger an SL.
        recent = [float(c) for c in reversed(closes) if c is not None][:5]
        median = None
        if recent:
            recent.sort()
            median = recent[len(recent) // 2]

        # Yahoo's meta "regularMarketPrice" is a LIVE quote, preferred
        # over the median — but sanity-checked against it (if live quote
        # diverges 1%+ from median it's a glitch, use median instead).
        live = result0.get("meta", {}).get("regularMarketPrice")
        if live is not None:
            live = float(live)
            if median is None or abs(live - median) / median <= 0.01:
                return live
            raise _YahooGlitch(
                f"live={live} vs median={median} — divergence too big"
            )

        return median

    try:
        price = _single(symbol)
        if price is not None:
            return price
        if fallback:
            price = _single(fallback)
            if price is not None:
                return price
        return get_external_price(asset)
    except _YahooGlitch as g:
        logger.warning(f"[PRICE] Yahoo glitch for {asset}: {g} — trying external sources")
        ext = get_external_price(asset)
        if ext is not None:
            return ext
        if fallback:
            try:
                return _single(fallback)
            except Exception:
                pass
        return None
    except Exception as primary_err:
        if fallback:
            try:
                price = _single(fallback)
                if price is not None:
                    return price
            except Exception as fb_err:
                logger.error(f"[PRICE ERROR] {asset}: primary={primary_err} fallback={fb_err}")
        else:
            logger.error(f"[PRICE ERROR] {asset}: {primary_err}")
        return get_external_price(asset)


def get_candles(asset: str = "btc") -> dict:
    """
    Fetch all three timeframes for any configured asset.
    Raises RuntimeError with a descriptive message on failure.
    """
    _asset_cfg(asset)   # validates + raises a clear error for unknown assets

    tf1  = get_asset_tf(asset, "1min")
    tf5  = get_asset_tf(asset, "5min")
    tf15 = get_asset_tf(asset, "15min")

    return {
        "asset":  asset.upper(),
        "price":  tf5["price"],
        "open":   tf5["open"],
        "close":  tf5["close"],
        "high":   tf5["high"],
        "low":    tf5["low"],
        "volume": tf5["volume"],
        "volume_missing": tf5.get("volume_missing", tf5["volume"] is None),
        "timeframes": {
            "1m":  tf1["close"],
            "5m":  tf5["close"],
            "15m": tf15["close"],
        },
    }


def _bars_from_payload(result0: dict) -> list[dict]:
    """Turn a Yahoo chart payload into a list of {ts,open,high,low,close}."""
    timestamps = result0.get("timestamp") or []
    quote      = (result0.get("indicators", {}).get("quote") or [{}])[0]
    opens   = quote.get("open")  or []
    highs   = quote.get("high")  or []
    lows    = quote.get("low")   or []
    closes  = quote.get("close") or []

    bars: list[dict] = []
    for i, ts in enumerate(timestamps):
        if ts is None:
            continue
        o = opens[i]  if i < len(opens)  else None
        h = highs[i]  if i < len(highs)  else None
        l = lows[i]   if i < len(lows)   else None
        c = closes[i] if i < len(closes) else None
        if None in (o, h, l, c):
            continue
        bars.append({
            "ts":    int(ts),
            "open":  float(o),
            "high":  float(h),
            "low":   float(l),
            "close": float(c),
        })
    return bars


# ── recent 1-minute bars (used by the trade monitor) ─────────────────────
#
# A short-lived cache so that N open trades on the same asset, plus any
# manual command in the same moment, share ONE HTTP request instead of
# hammering Yahoo. Yahoo rate-limits aggressively (HTTP 429) and a
# rate-limited monitor silently stops closing trades.
_RECENT_TTL   = 25          # seconds
_recent_cache: dict[str, tuple[float, list[dict]]] = {}


def get_recent_bars(asset: str = "btc", limit: int = 20) -> list[dict] | None:
    """
    Last `limit` ONE-MINUTE bars for `asset`, oldest first, each carrying its
    own unix `ts`. Returns None on failure (caller skips this cycle).

    Two things here matter a great deal, and both were bugs before:

    1. TIMESTAMPS. The old helper returned a single merged high/low for the
       last 3 bars with no timestamps attached. The trade monitor therefore
       evaluated a brand-new trade against the three minutes that happened
       BEFORE it was opened — so a large share of trades were closed as
       SL or TP1 on their very first poll, using price action that predates
       the entry. Returning per-bar timestamps lets the monitor discard
       every bar that started before the entry.

    2. WEIGHT. The old helper called get_asset_tf(), which pulls FIVE DAYS
       of 1-minute candles (~7000 points) and demands >=200 closed candles,
       with up to 3 retries. Doing that once a minute per open asset is a
       fast route to an HTTP 429. This uses range=1d and asks for no
       minimum, which is all the monitor ever needed.

    The still-forming candle is deliberately KEPT: its high/low is real
    traded price action, and including it is what lets a TP that is touched
    and immediately given back still register.
    """
    a   = str(asset).lower()
    now = _time.time()

    cached = _recent_cache.get(a)
    if cached and (now - cached[0]) < _RECENT_TTL:
        return cached[1][-limit:]

    cfg = _asset_cfg(a)

    def _pull(sym: str) -> list[dict]:
        url    = YF_URL.format(symbol=sym)
        params = {"interval": "1m", "range": "1d"}
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                r = requests.get(url, params=params, headers=YF_HEADERS, timeout=15)
                r.raise_for_status()
                payload = r.json()
                result  = (payload.get("chart", {}) or {}).get("result")
                if not result:
                    return []
                return _bars_from_payload(result[0])
            except Exception as e:
                last_err = e
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"[BARS] {a.upper()} attempt {attempt + 1}/{MAX_RETRIES} "
                        f"failed — retrying in {delay:.0f}s. {e}"
                    )
                    _time.sleep(delay)
        if last_err:
            raise last_err
        return []

    bars: list[dict] = []
    try:
        bars = _pull(cfg["symbol"])
        if not bars and cfg.get("fallback"):
            bars = _pull(cfg["fallback"])
    except Exception as e:
        logger.warning(f"[BARS] {a.upper()} 1m fetch failed: {e}")
        # Serve slightly stale cache rather than blinding the monitor
        if cached:
            logger.info(f"[BARS] {a.upper()} using stale cache ({int(now - cached[0])}s old)")
            return cached[1][-limit:]
        return None

    if not bars:
        return cached[1][-limit:] if cached else None

    bars.sort(key=lambda b: b["ts"])
    _recent_cache[a] = (now, bars)
    return bars[-limit:]
