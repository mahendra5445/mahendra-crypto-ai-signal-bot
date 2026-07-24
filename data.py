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

# Log a warning (but don't block the signal) when the newest closed
# candle is older than this many hours.
MAX_STALE_H = 8

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

    # ── Stale-data warning ────────────────────────────────────────────────
    if last_ts is not None:
        age_h = (_time.time() - last_ts) / 3600
        if age_h > MAX_STALE_H:
            logger.warning(
                f"[STALE] {label} {interval_key}: last candle is "
                f"{age_h:.1f}h old — market may be closed."
            )

    # Spot forex/metals tickers report zero volume on Yahoo.
    # Pass None so strategy knows "no data" rather than "zero volume".
    if sum(volumes_f) == 0:
        volumes_f = None

    return {
        "open":    opens_f,
        "close":   closes_f,
        "high":    highs_f,
        "low":     lows_f,
        "volume":  volumes_f,
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
        "timeframes": {
            "1m":  tf1["close"],
            "5m":  tf5["close"],
            "15m": tf15["close"],
        },
    }


def get_recent_range(asset: str = "btc", bars: int = 3) -> dict | None:
    """
    Last `bars` 1-minute candles ka HIGH / LOW / last CLOSE.

    KYUN: trade_monitor pehle get_latest_price() use karta tha, jo aakhri 5
    one-minute CLOSES ka MEDIAN deta hai. Wo do wajah se wins ko chupa
    raha tha:
      1. Median jaan-boojh kar spikes ko smooth kar deta hai -- aur TP
         aksar spike par hi hit hota hai.
      2. Monitor har 120s par ek hi number dekhta tha. Do poll ke beech
         TP touch ho kar wapas aa jaye to wo poori tarah invisible tha.
    SL ke saath ye problem nahi hoti: price SL ke paar jaake wahin rehta
    hai, to agla poll use pakad hi leta hai. Isliye SL hamesha register
    hota tha aur TP aksar nahi -- win rate artificially 5% tak gir gaya.

    High/low use karne se dono taraf ek jaisa insaaf hota hai.
    """
    try:
        tf = get_asset_tf(asset, "1min")
    except Exception:
        return None

    highs = [h for h in (tf.get("high") or []) if h is not None][-bars:]
    lows  = [l for l in (tf.get("low")  or []) if l is not None][-bars:]
    closes = [c for c in (tf.get("close") or []) if c is not None]

    if not highs or not lows or not closes:
        return None

    return {
        "high":  float(max(highs)),
        "low":   float(min(lows)),
        "close": float(closes[-1]),
    }
