# Changelog — line-by-line audit pass (25 Jul 2026)

A full line-by-line audit found **no correctness bugs** in the live bot.
All 23 bundled checks pass and independent probes confirm the previous
fixes are intact (timestamped bar replay, win-rate accounting,
price-aware rounding, original_sl, burst controls).

Two genuine improvements were applied; two items were deliberately left
as-is because changing them would add risk for no real benefit.

## Applied

1. **One shared clock for the whole bot** (`clock.py`, new).
   The daily trade cap, the daily-summary schedule, and the timestamp on
   each trade used to call `datetime.now()` independently — the server's
   clock (UTC on Railway). They now all go through `clock.py`, which has a
   single configurable offset: `BOT_UTC_OFFSET_MIN`, **default 330 (IST)**.
   - The trading day now rolls over at **midnight IST**, not midnight UTC.
   - The daily digest now fires at **21:00 IST** (`SUMMARY_HOUR = 21`).
   - Set `BOT_UTC_OFFSET_MIN=0` in Railway for plain UTC, or any other
     offset if you move the bot.
   Files touched: `clock.py` (new), `trade_tracker.py`, `guards.py`,
   `daily_summary.py`, `README.md`.

2. **Backtest session restore is now exception-safe** (`backtest.py`).
   `run_asset()` monkey-patches `strategy.get_current_session` while
   replaying bars. If `get_signal`/`simulate_trade` ever raised mid-loop,
   the real function was left patched for the rest of the process. It is
   now wrapped in `try/finally`, so the original is always restored.
   (Offline tool only — no effect on the live bot.)

## Deliberately NOT changed (correct as-is)

- **Same-bar TP1 + SL counts as a full loss.** When one 1-minute bar's
  range touches both TP1 and the original stop on a fresh trade, the
  monitor conservatively records the stop. Intra-bar order is unknowable
  from OHLC, so this is the standard pessimistic convention. Effect: the
  reported win rate is a slight *under*-estimate, never an over-estimate.
  Fixing it would require tick data.

- **Persistence runs inside the trade lock.** `save`/`update`/`mark_tp*`
  write state while holding the async lock. This is what makes each
  trade decision atomic. Moving the write outside the lock would open a
  race (an older state snapshot overwriting a newer one) for a bot that
  writes a few times a day — not worth it. The brief pause is well under
  the 60-second monitor interval.

## Still your job (unchanged, operational)

- Add a **Postgres plugin** in Railway so `DATABASE_URL` is set. Without
  it, every redeploy wipes trades/history and the guards start from zero.
- Push to GitHub.
- Reset stats to zero for a clean measurement baseline.
- (Optional) `BOT_UTC_OFFSET_MIN` is already IST by default; change only
  if you want a different day boundary.
