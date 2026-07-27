# Railway Deploy Guide

## 1. Create the project
Railway dashboard → New Project → Deploy from GitHub (or upload this build).

`railway.toml` / `Procfile` start the bot with: `python main.py`

## 2. Required environment variables

| Variable | Required | Notes |
|---|---|---|
| `BOT_TOKEN` | **Yes** | From @BotFather |
| `CHANNEL_ID` | **Yes** | `@channel` or numeric id; bot must be admin with Post Messages |
| `ADMIN_IDS` | **Yes** | Comma-separated Telegram user IDs allowed to run commands |
| `DATABASE_URL` | **Required** | Add the **Postgres** plugin — Railway sets this automatically. Bot refuses to start without it unless `ALLOW_JSON_PERSISTENCE=1` (local only). |

Optional:

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `data` | JSON fallback only (not durable without a volume) |
| `LOG_DIR` | — | Set only if you mount a volume for file logs |
| `ENABLE_FILE_LOGS` | off | Set `1` to also write rotating file logs |
| `BOT_UTC_OFFSET_MIN` | `330` | Day boundary (330 = IST) |
| `LIVE_PRICE_DRIFT_MAX` | `0.003` | Abort entry if live quote drifts >0.3% from signal bar |
| `MAX_STALE_H` | `0.5` | Reject candles older than 30 minutes |

## 3. Postgres (required for production)

Add the Railway **Postgres** plugin so `DATABASE_URL` is set.

Without Postgres the bot falls back to JSON on the container disk. That
filesystem is **ephemeral**: every redeploy wipes open trades and history,
and risk guards restart from zero.

Confirm in logs: `[PERSISTENCE] Postgres backend ready` and
`[INIT] Persistence backend: postgres`.

## 4. Make the bot a channel admin
Telegram → channel → Administrators → add the bot → enable **Post Messages**.

## 5. Optional volume
A volume is **optional** if Postgres is configured. Use a volume only if you
want file logs (`LOG_DIR=/data/logs`) or local JSON backups.

## 6. Deploy & verify
1. Deploy and watch logs for background tasks started.
2. DM the bot `/start` from an `ADMIN_IDS` account.
3. Confirm auto-signals appear in the channel after the warm-up delay.
4. Restart the service, then `/history` — trades should still be there (Postgres).
