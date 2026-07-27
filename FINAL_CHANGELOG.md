# FINAL_CHANGELOG.md
## Production build v3 — 2026-07-27

Adds a **soft RSI risk model** across all 12 assets. Core trend/momentum strategy,
risk calculator, persistence, Telegram commands, and Railway deploy path are unchanged.

---

## v3 — RSI Risk Model

| Item | Change |
|---|---|
| BUY score | Graduated penalties at RSI 70–79 / 80–84 / ≥85 |
| SELL score | Penalties at RSI ≤30 / ≤20 |
| Confidence | Haircuts on High / Extreme RSI |
| Position size | RSI ≥85 → max 50%, never Full Size |
| Telegram | `RSI : {value} {status}` (Normal / High / Extreme OB/OS) |
| Hard-block | **None** — trades still allowed when other gates pass |

Details: `STRATEGY_CHANGELOG.md`.

---

## Carried forward (v1 / v2)

- MUST/SHOULD remediations (C-001…, H-*, M-*, L-*)
- Production audit fixes P-001…P-006 (Postgres fail-closed, persist bool,
  notify-then-save, entry-minute bars, required `DATABASE_URL`, test `__main__`)

**Still not applied:** Wilder RSI/ADX/ATR rewrite, Binance-primary OHLC, session
VWAP rewrite, Telegram webhook migration, DST zoneinfo.

---

## Verification

| Check | Result |
|---|---|
| `compileall` | PASS |
| `test_fixes.py` | **63/63** |
| Import-all runtime modules | PASS |
| All 12 symbols share `get_signal` | PASS |
| Production audit | **PASS** (0 Critical / 0 High) |

---

## Env

Required: `BOT_TOKEN`, `CHANNEL_ID`, `ADMIN_IDS`, `DATABASE_URL`  
Local JSON-only: `ALLOW_JSON_PERSISTENCE=1`
