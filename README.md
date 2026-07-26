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

## Honest caveats
- SMC constructs (order blocks, breaker/mitigation blocks, inducement, etc.) have no single industry-standard formula — these are reasonable, configurable rule-based approximations, not "the" definition.
- The AI score is a transparent weighted-vote system with a self-learning weight update, not a trained ML model — that's a realistic and auditable starting point; a real ML classifier would need labeled multi-month/multi-symbol data.
- No exchange execution — this is a signal + backtest engine. Wiring it to place live orders is a separate, higher-stakes step (API keys, order management, slippage handling).
