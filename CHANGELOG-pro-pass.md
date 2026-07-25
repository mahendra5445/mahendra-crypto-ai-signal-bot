# Pro pass — burst control & gate tightening

Focused pass on the reported symptom: **the bot posted ~3 trades within a
minute of deploy.** Full re-audit of the trading path; the earlier PRO fixes
(timestamped bar replay, win-rate accounting, price-aware rounding,
original-SL preservation) were verified intact and still pass their tests.

## Root cause of the startup burst

Three things stacked up:

1. **No warm-up.** `auto_signal_job` ran its first full 12-coin scan the
   instant the process booted — the moment data is most likely stale after a
   redeploy.
2. **No per-cycle cap.** The 12 coins are highly correlated, so a clean trend
   makes several qualify in the *same* scan. Every qualifier opened, so one
   scan could post 3–4 near-identical entries at once.
3. **Ephemeral storage.** On a Railway Trial (JSON, no volume) every redeploy
   wipes trade state, so the guards start from zero and can't throttle the
   burst — and it repeats on every deploy.

## Fixes

- **`STARTUP_DELAY_SEC` (default 60).** Warm-up before the first scan. Stamps
  the watchdog heartbeat while waiting so the delay isn't mistaken for a stuck
  loop.
- **`MAX_NEW_TRADES_PER_CYCLE` (default 2).** Hard cap on new entries opened
  per scan. `_check_asset` now returns whether it opened a trade; the loop
  counts opens and stops once the cap is hit, so remaining coins wait for the
  next cycle instead of all firing together.
- **`MIN_CONFIRMATIONS` raised 8 → 9, now env-tunable** (with `MIN_SCORE`) in
  `config.py`. The code's own docstrings already treated 9 as the intended
  floor; 8 was a leftover that auto-posted the weakest "Quarter Size" tier as
  a real tracked trade. Lower it back to 8 via env if you deliberately want
  more, lower-quality signals.

## Not changed (verified correct, left as-is)

- Single-bar SL-before-TP pessimism (defensible convention).
- BE-after-TP1 counting as a win; consecutive-loss streak ignoring BE.
- Postgres/JSON persistence, effective_decimals wiring, analytics R model.

## Still on you (unchanged from before)

- Add the Railway **Postgres** plugin so `DATABASE_URL` is set — otherwise
  every redeploy still wipes open trades and history regardless of the above.

## Tests

`python test_fixes.py` → **23/23** (18 prior + 5 new burst-throttle checks).
