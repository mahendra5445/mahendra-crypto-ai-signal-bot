# STRATEGY_CHANGELOG.md
## RSI Risk Model — 2026-07-27

Soft RSI risk layer applied to **all supported pairs** via shared `get_signal()`
(`btc`, `eth`, `sol`, `bnb`, `xrp`, `doge`, `avax`, `ada`, `link`, `ton`, `sui`, `ltc`).

**Core strategy unchanged:** EMA / MACD / ADX / VWAP / Supertrend / Liquidity / SMC
formulas, confirmation counting, `MIN_CONFIRMATIONS` / `MIN_SCORE` gates, risk
calculator (`risk.py`), backtest modules, Telegram commands, and Railway deploy
manifests were **not** modified.

**No hard-blocks:** extreme RSI never forces `NO TRADE` by itself.

---

## What changed

### 1. RSI score penalties (`strategy.py`)

| Condition | Side | AI Score penalty |
|---|---|---|
| RSI 70–79 | BUY | −4 (small) |
| RSI 80–84 | BUY | −8 (medium) |
| RSI ≥ 85 | BUY | −15 (strong) |
| RSI ≤ 30 | SELL | −4 (small) |
| RSI ≤ 20 | SELL | −15 (strong) |

Penalties apply **after** the existing soft `W_RSI` band check (`52–75` / `25–48`)
and session adjustments, then scores are clamped to `0–100`. Final **AI Score**
and grade use the penalized value.

### 2. Confidence haircuts

| RSI region | Confidence reduction |
|---|---|
| High (70–84) | −6 |
| Extreme Overbought (≥85) or Extreme Oversold (≤20) | −15 |
| Soft oversold (21–30) on SELL | −4 |

### 3. Position size classification

If **RSI ≥ 85**:

- Suggested size capped at **50%**
- **Full Size / Standard Size (75%)** remapped to **Reduced Risk - Half Size**
- Trade is still allowed (no veto)

### 4. Telegram output (`formatter.py`)

RSI line now includes status:

```text
📊 RSI : 92.75 ❌ Extreme Overbought
```

Statuses:

| Status | When |
|---|---|
| ✅ Normal | RSI 21–69 |
| ⚠️ High | RSI 70–84 |
| ❌ Extreme Overbought | RSI ≥ 85 |
| ❌ Extreme Oversold | RSI ≤ 20 |

Posted trades also append an informational reason when a penalty applied, e.g.
`RSI risk penalty −15 (overbought)`.

### 5. Result fields added

`rsi_status`, `rsi_buy_penalty`, `rsi_sell_penalty` — available to formatters
and future analytics; unused by persistence schema.

---

## Example (ETH-like RSI 92.75)

Before RSI risk layer (illustrative): score ≈ 74, confidence 82%, Standard 75%.  
After:

- BUY penalty −15 → AI Score lower (may fall near / under `MIN_SCORE`)
- Confidence −15 from base confirmation mapping
- Size capped at **50%** if still tradeable
- Telegram shows **❌ Extreme Overbought**

---

## Explicitly unchanged

- Indicator implementations in `indicators.py`
- Confirmation boolean list / `MIN_CONFIRMATIONS` logic
- `calculate_trade` / ATR stop model
- `backtest.py` / `backtest_data.py` simulation engine
- Admin command handlers / Railway start command
