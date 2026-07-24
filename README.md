# Mahendra Crypto AI Signal Bot

Telegram signal bot for 12 crypto pairs. Scans each asset on a fixed cycle,
posts qualifying setups to a channel, then tracks every trade to SL / TP1 /
TP2 / TP3 and reports real performance.

**Every number this bot prints comes from the live trade list.** There are no
hand-maintained counters, and no claimed win rates anywhere in this repo.

---

## Quick start

```bash
pip install -r requirements.txt

export BOT_TOKEN="123456:ABC..."        # from @BotFather
export CHANNEL_ID="@yourchannel"        # bot must be admin with Post Messages
export DATABASE_URL="postgresql://..."  # optional but strongly recommended

python main.py
python test_fixes.py                    # regression suite, no network needed
```

## Railway deployment

1. Connect the GitHub repo — `railway.toml` handles the build.
2. **Add the Postgres plugin.** Railway then sets `DATABASE_URL` for you.
   Without it the bot writes to the container filesystem, which is wiped on
   every deploy: open trades are orphaned and all history resets to zero.
   The startup log tells you which backend is live.
3. Set `BOT_TOKEN` and `CHANNEL_ID` in Variables.

Confirm it worked: restart the service, then send `/history` — the trades
should still be there.

---

## Commands

| Command | What it does |
|---|---|
| `/btc` `/eth` `/sol` … | Manual signal for that asset |
| `/signal` | Same as `/btc` |
| `/trend` | Multi-timeframe trend summary |
| `/stats [asset]` | Signal counts, TP/SL/BE breakdown, win rate |
| `/perf [asset]` | Expectancy, profit factor, drawdown, streaks — in R |
| `/guards` | Exposure, daily count, loss streak, pause state |
| `/history [asset]` | Last 10 trades with TP progress |

---

## How a trade is tracked

1. `strategy.py` scores the setup; only BUY/SELL setups continue.
2. Levels are re-priced off a near-live quote so the posted entry is not a
   stale 5-minute candle close.
3. `guards.py` decides whether the trade is allowed to open at all.
4. `trade_monitor.py` polls 1-minute bars every 60s and **replays them in
   chronological order, starting from the bar after entry.**
5. TP1 banks the first partial and moves the stop to breakeven —
   `original_sl` is kept so the R maths stays honest.

### Risk model

- Stop: `2.5 × ATR(5m)`, floored at 0.15% of price, widened 40% in thin
  (Asian / off-hours) sessions.
- Targets: **TP1 = 1.2R, TP2 = 2.0R, TP3 = 3.0R.**
- Precision is derived from the live price, not hardcoded per coin.

### Risk guards (all tunable via env vars)

| Variable | Default | Meaning |
|---|---|---|
| `MAX_OPEN_TRADES` | 4 | Concurrent open positions |
| `MAX_TRADES_PER_DAY` | 12 | Daily signal cap |
| `MAX_CONSEC_LOSSES` | 4 | Losing streak that trips the breaker |
| `LOSS_PAUSE_SEC` | 21600 | Pause length after the breaker trips (6h) |
| `SIGNAL_CYCLE_SEC` | 900 | Full asset scan interval |
| `SIGNAL_COOLDOWN_SEC` | 900 | Per-asset silence after a signal |
| `MONITOR_INTERVAL_SEC` | 60 | SL/TP poll interval |

---

## Reading the performance report

`/perf` reports in **R** — multiples of the risk taken on each trade.

Win rate alone is misleading for a bot that scales out: 40% at +1.85R beats
70% at +0.2R. **Expectancy is the number that decides whether the strategy
works.** Positive = the edge is real; negative = it is not, regardless of how
good the win rate looks.

The R figures assume a **50 / 25 / 25 scale-out** at TP1 / TP2 / TP3. That
assumption is stated on the report itself. Outcomes settle at:

| Outcome | R |
|---|---|
| Stop, no TP1 | −1.00 |
| Breakeven after TP1 | +0.60 |
| Breakeven after TP2 | +1.10 |
| Full TP3 | +1.85 |

---

## Backtesting

```bash
python backtest.py --days 90                      # all 12 coins
python backtest.py --days 30 --assets btc,eth,sol
python backtest.py --days 90 --fee-bps 0          # frictionless, for comparison
```

Data comes from Binance's public klines endpoint (no API key), cached in
`backtest_data/`. Yahoo is not usable here — it only serves about 7 days of
1-minute candles, and 7 days of one regime tells you nothing.

The engine imports the real `strategy.get_signal()`, `risk.calculate_trade()`
and `effective_decimals()` rather than reimplementing them, so the result
describes the code that actually runs. It reproduces the 15-minute scan
cycle, the per-asset cooldown, entry at the signal bar's close, and SL/TP
resolution on 1-minute bars with the same pessimistic same-bar rule as the
live monitor. The session filter is driven by each bar's own timestamp
instead of the wall clock.

**Read the cost line.** The report prints `Avg cost ... R per trade`. At
Binance taker fees (~20 bps round trip) and a stop of 0.5% of price, costs
are about 0.4R — so a 1.2R first target is really 0.8R after fees. If that
line is a large fraction of 1R, the target structure is wrong for the stop
size, and no amount of signal tuning fixes it.

### What the backtest cannot tell you

- The news filter is skipped, so it takes trades the live bot would sit out.
- Live entries come off a Yahoo quote; these are Binance closes. Real fills
  differ, and not in your favour.
- No order book: a market that gaps through the stop fills worse than the
  stop price. This assumes you get it.
- Only pairs that still exist today are in the history.
- It is **one historical path**. A good number is evidence, not proof, and
  re-tuning parameters until it improves is how you fit noise. Change
  settings, then re-check on a period you did not tune on.

---

## What was fixed, and why it mattered

The bot previously showed a 5.41% win rate over 84 signals. That was not the
strategy losing — it was four accounting and data bugs stacked on top of each
other. `test_fixes.py` asserts each one is fixed and also demonstrates the old
broken behaviour so the regression can't quietly return.

**1. Pre-entry bars closed trades instantly.** The monitor fetched "the last
3 one-minute bars" with no timestamps and evaluated them against trades that
had just opened. A new trade was judged on three minutes of price action from
*before its own entry*, so a large share were closed — as a stop, or as a fake
TP1 that immediately moved the stop to breakeven — on their very first poll.
This is why `/history` showed rows where Entry and SL were identical.
Bars are now timestamped and anything starting before entry is discarded.

**2. Stop always beat target in a merged window.** Three bars were collapsed
into one high/low pair with the stop checked first, so any window touching
both sides became a loss no matter which came first. Bars are now replayed
one at a time in order. Inside a *single* bar the order is genuinely unknowable
from OHLC, so the stop still wins there — the standard pessimistic convention.

**3. Rounding destroyed the risk model on cheap coins.** AVAX was set to 2
decimals. At $6.26 the whole stop was one cent wide, so a "1:1.2" posted to
the channel was really 1:1.0. Precision now comes from `effective_decimals()`.

**4. Win rate could exceed 100%.** Wins counted any trade whose TP1 had been
hit *including still-open ones*, while the denominator counted only closed
trades. Wins now come from closed trades only.

Also fixed: `original_sl` is preserved when the stop moves to breakeven; the
monitor no longer pulls five days of 1-minute candles once a minute per asset
(a fast route to an HTTP 429 that silently stops all trade tracking); trades
survive a redeploy when Postgres is configured; and the three dead
`risk_FIXED_OPTION*.py` files plus seven stale documentation files — which
described settings the code never used and quoted invented win rates — are
gone.

---

## Project layout

```
main.py            entry point, command handlers
config.py          assets, tunables, effective_decimals()
strategy.py        signal scoring
indicators.py      EMA/RSI/MACD/ATR/ADX/VWAP/Supertrend
patterns.py        candlestick patterns
smart_money.py     liquidity sweeps
trend.py           multi-timeframe trend
session.py         London / NY / Asian session state
risk.py            stop and target placement
data.py            Yahoo fetch, Binance/Coinbase fallback, 1m bar cache
auto_signal.py     scan loop
trade_monitor.py   SL/TP tracking
trade_tracker.py   trade state, stats
guards.py          exposure and loss-streak breakers
analytics.py       R-multiple expectancy, profit factor, drawdown
persistence.py     Postgres with JSON fallback
daily_summary.py   daily digest
watchdog.py        stuck-loop alerts
news.py            high-impact news pause
notify.py          channel posting
formatter.py       message layout
test_fixes.py      regression suite
backtest.py        historical replay engine
backtest_data.py   Binance klines downloader + cache
```

---

## A note on expectations

Signals are generated from Yahoo Finance data, which is not an exchange feed:
prices can lag, gap, and occasionally glitch. The bot filters for this, but it
means fills you'd get on a real exchange will differ from the levels posted.
Treat `/perf` as a measurement of the *signal logic*, not of a live account.

Start the statistics from zero after deploying these fixes — every number
collected before them was produced by the bugs described above.
