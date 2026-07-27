# FINAL_CHANGELOG.md
## Production build v2 — 2026-07-27

Follow-up to the remediation build. Addresses every confirmed issue from
`FINAL_PRODUCTION_AUDIT.md` Round 1. **0 Critical / 0 High remaining.**

---

## v2 fixes (this release)

| ID | Severity | Change |
|---|---|---|
| **P-001** | Critical | Postgres load failures fail closed — no silent JSON fallback that can wipe durable state |
| **P-002** | High | `save_trades_to_disk` / `persist_snapshot` return `bool`; auto-signal & monitor alert on persist failure |
| **P-003** | High | After successful channel notify, always save+track (no post-notify `can_open` orphan path) |
| **P-004** | Medium | Monitor includes entry-minute Yahoo 1m candle (`bars_usable_after_entry`) |
| **P-005** | High | `DATABASE_URL` required unless `ALLOW_JSON_PERSISTENCE=1`; `post_init` refuses non-ready Postgres when required |
| **P-006** | Low | `test_fixes.py` only runs under `__main__` |

Also: startup logs recovered open-trade count from Postgres for restart visibility.

---

## Carried forward from v1 remediation

All prior MUST FIX / SHOULD FIX items remain in place (C-001…C-005, H-005/006/008/009/011–014/016/017, M-007/010–013/019–021, L-008/010/014, plus safe OPTIONAL items). See earlier changelog history and `IMPLEMENTATION_SUMMARY.md`.

**Still not applied (DO NOT APPLY):** Wilder RSI/ADX/ATR, Binance-primary OHLC, session VWAP, Telegram webhook migration, DST zoneinfo.

---

## Verification

| Check | Result |
|---|---|
| `compileall` | PASS |
| `test_fixes.py` | **45/45** |
| Postgres round-trip + restart recovery | PASS |
| Production audit Round 2 | **0 Critical / 0 High** |

---

## Env (production)

Required: `BOT_TOKEN`, `CHANNEL_ID`, `ADMIN_IDS`, `DATABASE_URL`  
Local JSON-only opt-out: `ALLOW_JSON_PERSISTENCE=1`
