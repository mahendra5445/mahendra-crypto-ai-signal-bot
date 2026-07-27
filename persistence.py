"""
Trade persistence with two backends.

  1. Postgres, when DATABASE_URL is set (Railway sets this automatically the
     moment you add a Postgres plugin to the project).
  2. Atomic JSON file, otherwise (local/dev only — see ALLOW_JSON_PERSISTENCE).

WHY THIS MATTERS: Railway's container filesystem is EPHEMERAL. Without a
volume — and a Trial plan does not get one — every deploy, restart and
crash wipes the JSON file. That means open trades are orphaned (never
monitored again, never closed, never reported) and the whole trade history
resets to zero, which makes every statistic the bot has ever printed
meaningless. Postgres is the fix; the JSON path stays for local runs.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

DATA_DIR    = os.getenv("DATA_DIR", "data")
TRADES_FILE = os.path.join(DATA_DIR, "trades.json")

_STATE_KEY = "trades"

# Set True when Postgres was configured but load/save is failing — operators
# must not trust durability in that mode.
persistence_degraded: bool = False

# ── optional Postgres driver ──────────────────────────────────────────────
_pg = None
_Json = None
if DATABASE_URL:
    try:
        import psycopg2  # type: ignore
        from psycopg2.extras import Json  # type: ignore
        _pg = psycopg2
        _Json = Json
    except ImportError:
        logger.error(
            "[PERSISTENCE] DATABASE_URL is set but psycopg2 is not installed — "
            "falling back to JSON files (data WILL be lost on redeploy). "
            "Add psycopg2-binary to requirements.txt."
        )
        persistence_degraded = True


def _pg_connect():
    # Railway hands out postgres:// URLs; psycopg2 wants postgresql://
    url = DATABASE_URL
    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return _pg.connect(url, connect_timeout=10)


def _pg_init() -> None:
    with _pg_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS bot_state ("
            "  key   TEXT PRIMARY KEY,"
            "  value JSONB NOT NULL,"
            "  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ")"
        )
        conn.commit()


def _pg_canary() -> None:
    """Write+read a canary key so we refuse to claim Postgres if JSONB binding is broken."""
    with _pg_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bot_state (key, value, updated_at) "
            "VALUES (%s, %s, NOW()) "
            "ON CONFLICT (key) DO UPDATE "
            "SET value = EXCLUDED.value, updated_at = NOW()",
            ("__canary__", _Json({"ok": True})),
        )
        cur.execute("SELECT value FROM bot_state WHERE key = %s", ("__canary__",))
        row = cur.fetchone()
        conn.commit()
    if not row or not row[0]:
        raise RuntimeError("Postgres canary read returned empty")


_pg_ready = False
if _pg is not None and _Json is not None:
    try:
        _pg_init()
        _pg_canary()
        _pg_ready = True
        logger.info("[PERSISTENCE] Postgres backend ready")
    except Exception as e:
        persistence_degraded = True
        logger.error(
            f"[PERSISTENCE] Postgres init/canary failed ({e}) — using JSON fallback "
            "(NOT durable on Railway)"
        )


def backend_name() -> str:
    return "postgres" if _pg_ready else "json-file"


def is_degraded() -> bool:
    return persistence_degraded or (bool(DATABASE_URL) and not _pg_ready)


def postgres_required() -> bool:
    """Production default: Postgres is required unless explicitly opted out."""
    return os.getenv("ALLOW_JSON_PERSISTENCE", "").strip().lower() not in (
        "1", "true", "yes",
    )


# ── load ──────────────────────────────────────────────────────────────────

def _load_json() -> tuple[list, int]:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(TRADES_FILE):
        return [], 1
    try:
        with open(TRADES_FILE, encoding="utf-8") as f:
            return _unpack(json.load(f))
    except Exception as e:
        logger.error(f"[PERSISTENCE] Corrupt trades file ({e}); backing up, starting fresh.")
        try:
            os.rename(TRADES_FILE, TRADES_FILE + ".corrupt")
        except OSError:
            pass
        return [], 1


def _unpack(data: dict) -> tuple[list, int]:
    trades  = data.get("trades", []) or []
    next_id = data.get("next_id")
    if not next_id or not isinstance(next_id, int):
        ids     = [t.get("id", 0) for t in trades if isinstance(t.get("id"), int)]
        next_id = (max(ids) + 1) if ids else 1
    return trades, next_id


def load_trades_from_disk() -> tuple[list, int]:
    """
    Returns (trades_list, next_id). next_id is always > every existing id.

    P-001: when Postgres is the active backend, load failures fail closed —
    never silently replace durable state with ephemeral JSON.
    """
    global persistence_degraded

    if _pg_ready:
        try:
            with _pg_connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT value FROM bot_state WHERE key = %s", (_STATE_KEY,))
                row = cur.fetchone()
            if row and row[0]:
                trades, next_id = _unpack(row[0])
                logger.info(f"[PERSISTENCE] Loaded {len(trades)} trades from Postgres")
                return trades, next_id
            logger.info("[PERSISTENCE] Postgres empty — starting fresh")
            return [], 1
        except Exception as e:
            persistence_degraded = True
            logger.critical(
                f"[PERSISTENCE] Postgres load failed ({e}) — refusing JSON fallback "
                "to avoid overwriting durable state with empty/ephemeral data"
            )
            raise RuntimeError(
                f"Postgres trade-state load failed: {e}. "
                "Fix DATABASE_URL / connectivity before starting."
            ) from e

    trades, next_id = _load_json()
    logger.info(f"[PERSISTENCE] Loaded {len(trades)} trades from JSON")
    return trades, next_id


# ── save ──────────────────────────────────────────────────────────────────

def _save_json(payload: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = TRADES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TRADES_FILE)   # atomic on POSIX


def save_trades_to_disk(trades: list, next_id: int) -> bool:
    """
    Persist trade state. Returns True on success, False if no backend saved.

    P-002: callers must check the return value — silent save failure previously
    left in-memory opens unrecoverable after restart.
    """
    global persistence_degraded
    payload = {"next_id": next_id, "trades": trades}

    if _pg_ready:
        try:
            with _pg_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO bot_state (key, value, updated_at) "
                    "VALUES (%s, %s, NOW()) "
                    "ON CONFLICT (key) DO UPDATE "
                    "SET value = EXCLUDED.value, updated_at = NOW()",
                    (_STATE_KEY, _Json(payload)),
                )
                conn.commit()
            return True
        except Exception as e:
            persistence_degraded = True
            logger.critical(
                f"[PERSISTENCE] Postgres save failed ({e}) — "
                "JSON fallback is NOT durable on Railway"
            )
            # When Postgres is required, do not pretend JSON saved durably.
            if postgres_required():
                return False

    try:
        _save_json(payload)
        return True
    except Exception as e:
        persistence_degraded = True
        logger.error(f"[PERSISTENCE] Save trades error: {e}")
        return False
