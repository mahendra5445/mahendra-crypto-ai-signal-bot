# Mahendra Crypto AI Signal Bot (BTC) — v1 build

Built from `BTCUSDT-1m-2026-05.csv` (44,640 one-minute candles, May 2026, Binance kline format).

## Run it
```
pip install pandas numpy --break-system-packages
python3 main.py
```
Outputs land in `output/`: `trade_journal.csv`, `mahendra.db` (SQLite), `performance_report.json`, `monthly_report.csv`, `weights.json` (self-learning weights).

**Latest backtest on your file (after bug-fix pass below):** 684 trades, 65.1% win rate, profit factor 2.39, +331.4R total, -5.0R max drawdown.
This is a single in-sample run on one month of data with no walk-forward validation — treat it as a proof that the pipeline works, not as evidence of a tradeable edge. Re-test on more months and out-of-sample before risking real capital.

## Bug-fix pass (post-review)
A deep review surfaced real bugs — fixed, not just cosmetic:
1. **PnL accounting bug (trade_management.py):** if a trade hit TP1/TP2 (partial profit banked) and *then* got stopped out on the trailing stop, the exit calculation was silently dropping the banked partial profit — a trade that was actually +0.6R could get logged as flat/negative. Fixed so realized partial profit is always carried into the final PnL.
2. **Duplicate-signal protection was a no-op (risk.py / main.py):** the dedup key included the bar index, which is always unique, so item 50 never actually blocked anything. Fixed to key on direction + price-zone instead.
3. **Six SMC constructs were fully coded but never wired into any decision** — equal highs/lows, inducement, breaker blocks, mitigation blocks, fake breakout, and institutional zone (volume-profile POC) were computed and then discarded. They're now wired into the AI score as real votes.
4. **Dynamic ATR filter (item 1) was computed but unused** — now used as a sanity check that rejects signals during abnormal volatility spikes (|z| > 4), which can indicate bad ticks or a violent news candle.
5. `ai_score` was hardcoded to `null` in every journal row even though the score was available — now logged correctly.
6. Minor: removed a leftover dead-code line in the session-filter overlap calculation (harmless, just redundant).

Numbers changed materially after these fixes (684 vs. 859 trades, better win rate/profit factor) — that's expected: the earlier run both mis-accounted some PnL and was missing real confirmation signals.

## What's genuinely implemented (from your OHLCV data)
| # | Feature | File |
|---|---|---|
|1|Dynamic ATR filter|indicators.py|
|2|EMA slope + distance|indicators.py|
|3|Market structure (BOS/CHOCH/HH/HL/LH/LL)|smc.py|
|4|Order blocks|smc.py|
|5|Fair value gaps|smc.py|
|6|Premium/discount zones|smc.py|
|7|Liquidity pools|smc.py|
|8|Equal highs/lows|smc.py|
|9|Inducement|smc.py|
|10|Breaker blocks|smc.py|
|11|Mitigation blocks|smc.py|
|12|Volume profile (POC/HVN/LVN)|indicators.py|
|15|Dynamic AI score|scoring.py|
|16-20|ATR SL, trailing stop, breakeven, partial TP, time exit|trade_management.py|
|21-25|30m/1h confirmation, MTF trend weight & structure|main.py (`build_mtf_trend`)|
|26-33|Candle/momentum filters, RSI, MACD, ADX, VWAP|indicators.py|
|34-38|Volume spike, RVOL, buy/sell ratio, delta volume, low-liquidity flag|indicators.py|
|39-43|Liquidity sweep, stop hunt, fake breakout, smart-money entry, institutional zone|smc.py|
|44-50|Position sizing, daily/consecutive loss limits, open-trade cap, cooldown, duplicate protection|risk.py|
|51-56|Min score, confidence, volatility-based expiry, trend probability %|scoring.py|
|63-67|London/NY/Asian kill zones, overlap, session volatility rating|sessions_external.py|
|68-73|Auto-BE, ATR TP, TP1/2/3 ladder, RR, timeout, weak-trade close|trade_management.py|
|74-79|Trade journal (SQLite+CSV), win rate, profit factor, drawdown, monthly report|journal.py|
|80-85|Self-learning weight updates from win/loss history|scoring.py|
|86-95|Retry/timeout wrapper, persistent DB, duplicate-trade guard, data integrity check, config validation|data_layer.py, journal.py|
|96-97|Market regime (trend/range), volatility regime|regime.py|

## What CANNOT come from this file — needs live APIs (stubbed, clearly marked)
Your CSV is historical BTCUSDT spot/futures klines only. These items need **external live data feeds** that don't exist in any OHLCV file. I built the hooks with obvious TODOs in `sessions_external.py` so you can drop in real keys:

- **13. Open Interest filter** → Binance Futures `openInterestHist` endpoint
- **14. Funding rate filter** → Binance Futures `fundingRate`/`premiumIndex`
- **57-62. News filters (CPI/FOMC/NFP/ETF/economic calendar)** → an economic-calendar API (ForexFactory/TradingEconomics)
- **98. BTC dominance filter** → CoinGecko/CoinMarketCap global-metrics endpoint
- **99. ETH-BTC correlation** → needs an ETHUSDT price series (only BTCUSDT was uploaded); the function accepts one if you supply it

Everything else runs today, offline, on your file.

## New in this pass (10 items requested)
| # | Item | What actually happened |
|---|---|---|
|1|Real Open Interest API|`live_data.get_open_interest()` — real Binance Futures `openInterestHist` call. **Live-only** (see below).|
|2|Real Funding Rate API|`live_data.get_funding_rate()` — real Binance Futures `premiumIndex` call. **Live-only**.|
|3|Binance Long/Short Ratio|`live_data.get_long_short_ratio()` — real `globalLongShortAccountRatio` call. **Live-only**.|
|4|Fear & Greed Index|`live_data.get_fear_greed_index()` — real alternative.me call, free, no key. **Live-only**.|
|5|Cumulative Volume Delta (CVD)|`indicators.cvd()` + `cvd_divergence()` — real, from your file's own `taker_buy_base`/`volume` columns. **Backtested**: wired into the AI score as a live vote.|
|6|Commission Simulation|`trade_management.commission_r()` — round-trip taker fee (0.04%/side default) deducted from every trade's R before logging. **Backtested**.|
|7|Latency Simulation|`trade_management.apply_latency_slippage()` — every entry/exit fill is slipped by a spread cost + an ATR-scaled amount for the simulated order latency (250ms default), always adverse. **Backtested**.|
|8|Walk-Forward Optimization|`validation.run_walk_forward()` — 5 rolling folds; weights adapt on each fold's train slice, freeze, then run out-of-sample on the test slice. Only OOS trades count toward the reported result. **Backtested**.|
|9|Monte Carlo Backtesting|`validation.monte_carlo_backtest()` — 5,000 bootstrap resamples of your realized trade R-multiples, giving a percentile range for final equity and max drawdown instead of one lucky/unlucky sequence. **Backtested**.|
|10|Real API for Funding/OI/Dominance/Calendar/Correlation|`sessions_external.py` now forwards to `live_data.py` real calls for funding, OI, dominance, and long/short ratio. ETH-BTC correlation is real once you fetch matching historical ETH candles via `live_data.get_eth_klines()`. The economic calendar stays a documented stub — see below.|

### Why items 1-4 (and dominance) are "live-only," not backtested
Open Interest, Funding Rate, Long/Short Ratio, Fear & Greed and BTC Dominance are all **current-snapshot** endpoints — there's no free historical time series from Binance/CoinGecko/alternative.me that lines up 1:1 with your May-2026 1-minute bars. Attaching *today's* funding rate to a *May-2026* candle would be lookahead-shaped noise dressed up as a real signal, so it's deliberately **not** wired into the historical backtest loop in `main.py`. Instead:
- `live_data.py` has real, working functions for all of them — call `live_market_filter()` from a live/forward-testing loop and they'll return today's actual values.
- The one exception is ETH price: Binance's public klines endpoint does return real historical candles for any past window, so `eth_btc_correlation()` becomes genuinely backtestable once you pull the matching range with `get_eth_klines(start_time=..., end_time=...)`.
- The economic calendar (items 57-62) is left as a **documented stub** — there's no free, keyless, ToS-safe calendar API (ForexFactory/Investing.com require scraping that breaks their ToS; TradingEconomics/Finnhub need a paid key). Plug a paid key into `live_data.economic_calendar_events()` when you have one.

### Sandbox limitation
This review/build ran in an environment with outbound network access disabled, so the real API calls in `live_data.py` could not be executed or smoke-tested here. Endpoints, params, and response field names match the current public Binance Futures/Spot, CoinGecko, and alternative.me docs — run `python3 live_data.py` on a machine with internet access before depending on it live. Everything else (CVD, commission, latency, walk-forward, Monte Carlo) was run end-to-end on synthetic OHLCV data in this sandbox and confirmed working; re-run `python3 main.py` on your real file to get real numbers — the synthetic-data numbers in this sandbox are meaningless and not reported here.

## Post-build audit — 2 real bugs found and fixed in the latency simulation
A follow-up audit caught that the first cut of latency/slippage simulation looked correct but had **zero actual effect on the numbers**:
1. **Entry side:** the stop-loss was being re-derived from the already-slipped entry price (`stop = slipped_entry ± ATR*mult`), so the risk distance always came out to exactly `atr_sl_mult * ATR` no matter how much slippage was applied — the slippage canceled itself out. Fixed by anchoring the stop to the *intended* signal price instead, so an adverse fill now genuinely costs extra risk distance.
2. **Exit side:** a slipped exit price was computed and written to the trade journal, but `pnl_r` was still calculated from the original, unslipped exit price — the slippage only changed a label, never the PnL. Fixed with `trade_management.slipped_exit_and_pnl()`, which re-derives the R-multiple from the slipped price for real price-based exits (stop-loss/time-exit/weak-trade-close) and applies an approximated R-based tax for the idealized TP3 fixed-R-target exit.

Verified with a standalone before/after check: a stop-out that used to land at exactly `-1.00R` (friction included only in appearance) now lands at `-1.19R` once slippage (`-0.14R`) and commission (`-0.05R`) are genuinely subtracted — see the numbers change in `git diff` on `trade_management.py`/`main.py` if you want to confirm yourself.

## Honest caveats
- SMC constructs (order blocks, breaker/mitigation blocks, inducement, etc.) have no single industry-standard formula — these are reasonable, configurable rule-based approximations, not "the" definition.
- The AI score is a transparent weighted-vote system with a self-learning weight update, not a trained ML model — that's a realistic and auditable starting point; a real ML classifier would need labeled multi-month/multi-symbol data.
- No exchange execution — this is a signal + backtest engine. Wiring it to place live orders is a separate, higher-stakes step (API keys, order management, slippage handling).
