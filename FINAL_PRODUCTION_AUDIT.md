# FINAL_PRODUCTION_AUDIT.md
## Production audit — 2026-07-27 (pass after remediation)

**Verdict: PASS — 0 Critical, 0 High remaining.**

Scope: `/agent/prod` Mahendra Crypto AI Signal Bot (Railway + Telegram + Postgres).

---

## Audit rounds

| Round | Critical | High | Medium | Low | Outcome |
|---|---|---|---|---|---|
| 1 (initial) | 1 | 3 | 1 | 1 | Issues confirmed → fixed |
| 2 (re-audit) | 0 | 0 | 0 | 0 | **PASS** |

---

## Round 1 — confirmed issues (all fixed)

### P-001 — Critical — Postgres load silently fell back to JSON
- **Files:** `persistence.py` (`load_trades_from_disk`)
- **Bug:** On Postgres load failure after a successful canary, the bot loaded ephemeral JSON while still advertising the Postgres backend. A later save could overwrite durable state with incomplete data and orphan open trades after restart.
- **Fix:** Fail closed — set `persistence_degraded`, log critical, raise `RuntimeError`. No JSON fallback when `_pg_ready`.
- **Status:** Fixed

### P-002 — High — Persist failures swallowed
- **Files:** `persistence.py`, `trade_tracker.py`, `auto_signal.py`, `trade_monitor.py`
- **Bug:** `save_trades_to_disk` returned `None` and callers ignored failures. Channel posts / monitor updates could exist only in memory.
- **Fix:** Save returns `bool`. Callers log critical, alert the channel on failure, and do not claim durable tracking.
- **Status:** Fixed

### P-003 — High — Post-notify guard could orphan channel signals
- **Files:** `auto_signal.py`
- **Bug:** After a successful Telegram post, a second `can_open` check could refuse `save_trade`, leaving an untracked live signal and a sticky dedup key.
- **Fix:** Pre-notify guard is authoritative; after successful notify, always `save_trade` + persist.
- **Status:** Fixed

### P-004 — Medium — First-minute 1m bar skipped
- **Files:** `trade_monitor.py` (`bars_usable_after_entry`)
- **Bug:** Yahoo 1m `ts` is candle open. Mid-minute entries excluded the active candle, missing first-minute SL/TP.
- **Fix:** Floor `opened_ts` to the containing minute; still exclude prior full minutes.
- **Status:** Fixed

### P-005 — High — `DATABASE_URL` not required at startup
- **Files:** `config.py` (`require_env`), `main.py` (`post_init`)
- **Bug:** Railway could start on JSON-only ephemeral storage and wipe trades on redeploy.
- **Fix:** Require `DATABASE_URL` unless `ALLOW_JSON_PERSISTENCE=1`. `post_init` raises if Postgres is required but not ready. Startup logs recovered open trade count.
- **Status:** Fixed

### P-006 — Low — `test_fixes.py` ran on import
- **Files:** `test_fixes.py`
- **Bug:** Importing the module mutated env and called `SystemExit`.
- **Fix:** Logic behind `if __name__ == "__main__":` / `run_tests()`.
- **Status:** Fixed

---

## Round 2 — verification matrix

| Requirement | Result |
|---|---|
| Syntax errors | PASS (`compileall` + AST) |
| Import errors | PASS (all runtime modules) |
| Circular imports | PASS (static import graph) |
| Broken references | PASS |
| Duplicate conflicting runtime logic | PASS (only expected `trade_r` / CLI `main`) |
| TODO / FIXME / placeholders | PASS (none) |
| Railway compatibility | PASS (`railway.toml`, `Procfile`, Nixpacks, `python main.py`) |
| Telegram bot functionality | PASS (polling, admin allowlist, handlers, notify retries) |
| TradingView inbound webhooks | N/A by design (no inbound webhook; polling + Yahoo engine) |
| PostgreSQL persistence | PASS (canary + JSONB round-trip on Postgres 16) |
| Clean startup | PASS (`require_env` fail-closed; Application builds) |
| Trade recovery after restart | PASS (open trade `#42` reloaded from Postgres) |
| Automated regression suite | **45/45** passed |

---

## By design (not defects)

| Topic | Note |
|---|---|
| No TradingView inbound webhooks | Signals are generated internally; long polling only |
| Non-Wilder RSI/ATR/ADX | DO NOT APPLY — thresholds calibrated to current estimators |
| Yahoo primary OHLC | DO NOT APPLY Binance-as-primary |
| Cumsum VWAP | DO NOT APPLY session VWAP rewrite |
| JSON persistence | Local/dev only via `ALLOW_JSON_PERSISTENCE=1` |

---

## Evidence commands

```text
python3 -m compileall -q .
python3 test_fixes.py                 → 45/45
Postgres save → process reload → open trade present
BOT_TOKEN/CHANNEL_ID/ADMIN_IDS/DATABASE_URL startup smoke
```

**Production build is audit-clean for Critical and High.**
