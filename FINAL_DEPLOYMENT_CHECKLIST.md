# FINAL_DEPLOYMENT_CHECKLIST.md

Use before going live on Railway. Audit status: **PASS** (`FINAL_PRODUCTION_AUDIT.md`).

## A. Secrets & access

- [ ] `BOT_TOKEN` set (BotFather)
- [ ] `CHANNEL_ID` set (`@channel` or numeric id)
- [ ] Bot is **channel admin** with **Post Messages**
- [ ] `ADMIN_IDS` set (comma-separated Telegram user ids)
- [ ] Unauthorized accounts get “Unauthorized.”

## B. Persistence (critical)

- [ ] Railway **Postgres** plugin added
- [ ] `DATABASE_URL` present (bot **will not start** without it)
- [ ] Do **not** set `ALLOW_JSON_PERSISTENCE` on Railway
- [ ] Logs: `[PERSISTENCE] Postgres backend ready`
- [ ] Logs: `[INIT] Persistence backend: postgres`
- [ ] Logs: `[INIT] Recovered N open trade(s) from postgres`
- [ ] Restart once → open trades still in `/history` and still monitored

## C. Process & deploy

- [ ] Start command via `railway.toml` / `Procfile`: `python main.py`
- [ ] Four background tasks start (auto_signal, trade_monitor, watchdog, daily_summary)
- [ ] First scan waits `STARTUP_DELAY_SEC` (default 60s)

## D. Runtime sanity

- [ ] Admin `/start`, `/guards`, `/btc` work; manual replies show UNTRACKED banner
- [ ] Auto-signals only open tracked trades after successful channel notify **and** persist
- [ ] Persistence failure posts a channel warning (do not ignore)
- [ ] Stale Yahoo data skips the asset (no trade on STALE DATA)
- [ ] Watchdog alerts if signal/monitor heartbeats go silent

## E. TradingView / webhooks

- [ ] Confirm nothing sends TradingView alerts **to** this service — there is **no inbound webhook**
- [ ] Signals come from internal Yahoo → strategy → Telegram (long polling)

## F. Optional tuning

| Variable | Default | Purpose |
|---|---|---|
| `LIVE_PRICE_DRIFT_MAX` | `0.003` | Abort if live quote drifts >0.3% |
| `MAX_STALE_H` | `0.5` | Reject candles older than 30m |
| `MIN_CONFIRMATIONS` | `9` | Gate |
| `MIN_SCORE` | `62` | Gate |
| `BOT_UTC_OFFSET_MIN` | `330` | IST day boundary |
| `ALLOW_JSON_PERSISTENCE` | unset | Local/dev only |

## G. Post-deploy smoke

1. Deploy → Postgres ready + recovered open count in logs  
2. `/start` as admin  
3. Confirm a channel signal appears **and** `/history` shows it  
4. Redeploy → same open trades recovered  

## H. Rollback

Keep the previous working release. If Postgres canary fails, fix `DATABASE_URL` before starting — JSON fallback is not durable on Railway and is blocked when Postgres is required.
