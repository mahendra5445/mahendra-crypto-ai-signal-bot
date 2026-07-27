# STRATEGY_VALIDATION_REPORT.md
## Live ETH signal analysis — read-only validation

**Signal under review:** ETH BUY @ 1969.32  
**Report date:** 2026-07-27  
**Scope:** `strategy.py`, `indicators.py`, `risk.py`, `formatter.py`, `session.py`, `smart_money.py`, `trend.py`, `config.py`  
**Code modified:** None (analysis only)

---

## Executive summary

The ETH BUY is **fully explained by the current strategy code**. The bot did not ignore RSI — it evaluated RSI, **failed** the bullish RSI band (`52–75`), and still issued BUY because RSI is a **soft confirmation and score component**, not a hard veto.

From a **real trading performance** standpoint, a BUY at **RSI 92.75** during **Asian / Low Liquidity** is a **high-risk late-trend entry** with elevated immediate pullback probability. The implementation matches its design, but that design under-penalizes extreme overbought conditions.

---

## 1. Why BUY was generated

A BUY is emitted only when **both** gates pass (`strategy.py`):

```341:342:prod/strategy.py
    buy_ready = buy_all_true and buy_score >= MIN_SCORE
    sell_ready = sell_all_true and sell_score >= MIN_SCORE
```

Where:

- `buy_all_true` → `buy_confirmations >= MIN_CONFIRMATIONS` (default **9**)
- `buy_score >= MIN_SCORE` (default **62**)

For this ETH signal:

| Gate | Required | Actual | Pass? |
|---|---|---|---|
| Buy confirmations | ≥ 9 | **10** | ✅ |
| AI score | ≥ 62 | **74** | ✅ |
| Sell side competition | — | 4 confirmations, lower score | BUY wins |

Because `buy_ready` is true and `sell_ready` is false, `final_signal = "BUY"` with no tie-break needed.

### Confirmations that passed (10/11 countable)

| # | Check | Value in signal | Pass? |
|---|---|---|---|
| 1 | EMA bullish stack | EMA ✅ | ✅ |
| 2 | ADX ≥ 22 | ADX Strong | ✅ |
| 3 | Supertrend bullish | Supertrend ✅ | ✅ |
| 4 | Price > VWAP | VWAP ✅ | ✅ |
| 5 | MACD bullish + positive histogram | MACD Bullish | ✅ |
| 6 | **RSI in 52–75 band** | **RSI 92.75** | **❌** |
| 7 | ATR expansion | ATR Expansion in reasons | ✅ |
| 8 | MTF ≥ 2/3 bullish | 1M/5M/15M all Strong Bullish | ✅ |
| 9 | Bullish candle close | Bullish Candle Confirmed | ✅ |
| 10 | Volume ≥ 85% of 20-bar avg | Volume OK | ✅ |
| 11 | Volume spike ≥ 105% of avg | Volume Spike | ✅ |

**RSI is the only failed confirmation.** Ten others passed, exceeding the minimum of 9.

### Post-score adjustments

Starting weighted score (see §2): **82**  
Asian session (`session_active=False`): **−8**  
Final capped score: **74** → Grade **B**, Tier **Standard Size (75%)**

Confidence **82%** is not derived from AI score directly — it is mapped from confirmation count:

```408:409:prod/strategy.py
        if confirmations == 10:
            return 82.0
```

Ten confirmations → exactly **82.0% confidence**, independent of RSI failure.

---

## 2. Score contribution of every indicator

### Base weights (sum to 100 before bonuses)

```32:41:prod/strategy.py
W_EMA = 14
W_ADX = 9
W_SUPERTREND = 14
W_VWAP = 9
W_MACD = 9
W_RSI = 9
W_VOLUME = 9
W_ATR = 4
W_MTF = 14
```

### BUY-side score breakdown for this signal

| Component | Weight | Condition | Points earned |
|---|---|---|---|
| EMA stack | 14 | `ema20 > ema50 > ema200` | **14** |
| ADX | 9 | `adx ≥ 22` | **9** |
| Supertrend | 14 | trend == Bullish | **14** |
| VWAP | 9 | price > VWAP | **9** |
| MACD | 9 | line > signal, histogram > 0 | **9** |
| **RSI** | **9** | **`52 ≤ RSI ≤ 75`** | **0** |
| Volume | 9 | current vol ≥ 85% of 20-bar avg | **9** |
| ATR expansion | 4 | ATR > ATR MA | **4** |
| MTF alignment | 14 | ≥ 2/3 timeframes bullish | **14** |
| **Subtotal** | **100** | | **82** |

### Bonuses / penalties applied

| Adjustment | Rule | Applied? | Points |
|---|---|---|---|
| Pattern bonus | +5 if bullish pattern | Pattern = None | 0 |
| London/NY session bonus | +3 if London or NY in session name | Asian session | 0 |
| **Low-liquidity penalty** | **−8 if not `session_active`** | **Asian ⚠️** | **−8** |
| Score cap | clamp 0–100 | | |
| **Final AI score** | | | **74** |

### Not scored (informational only)

| Item | Shown in message | Scored? |
|---|---|---|
| Liquidity sweep (Buy Side) | Yes | No (H-006 demoted) |
| Pattern None | Yes | No bonus |
| Bollinger | Not shown | No |
| Session label | Yes | Penalty only, not a confirmation |

**Liquidity sweep "Buy Side"** means the latest high exceeded the prior 5-bar high (`smart_money.py`). It is displayed but does **not** add confirmations or score.

---

## 3. Why RSI = 92.75 did NOT block BUY

RSI is defined as a **band check**, not an overbought veto:

```163:164:prod/strategy.py
    rsi_bull = 52 <= rsi_value <= 75
    rsi_bear = 25 <= rsi_value <= 48
```

At **RSI 92.75**:

- `rsi_bull = False` (above upper band 75)
- `rsi_bear = False`
- **No hard `if rsi > X: return NO TRADE` exists anywhere**

Effects of RSI failure:

1. **−9 AI score points** (`W_RSI` not added) — already applied; score still 74
2. **−1 confirmation** — still 10 ≥ 9 minimum
3. **Excluded from Reasons list** — `build_reasons()` only lists passed checks; no `"RSI Healthy (...)"` line appears (consistent with failure)
4. **Still displayed in header** — `formatter.py` always prints raw RSI with no ✅/❌:

```56:56:prod/formatter.py
📊 RSI : {result['rsi']}
```

So RSI did not block BUY because the strategy treats RSI as **"ideal momentum zone" confirmation**, not **"must not be overbought" protection**. Failing RSI is allowed as long as enough other confirmations and score remain.

---

## 4. Intentional behavior or strategy bug?

| Lens | Assessment |
|---|---|
| **Code intent** | **Intentional.** RSI band 52–75 is a soft filter. `MIN_CONFIRMATIONS=9` was designed so signals can pass without every check (including RSI). |
| **Trading logic** | **Design weakness / performance bug.** Extreme overbought (92.75) is materially different from "RSI slightly outside band." The strategy does not distinguish moderate miss vs dangerous extension. |
| **UX consistency** | **Misleading.** RSI prints prominently without indicating it **failed** the strategy's own bullish RSI rule, while EMA/ADX/VWAP show ✅. |

**Conclusion:** Not a runtime bug — the bot executed the spec correctly. It **is** a strategy-logic gap for live performance: overbought extremes are under-penalized relative to trend/momentum factors.

---

## 5. Is the entry statistically safe?

**Moderate-to-low safety for a fresh long at market.**

Supporting factors:

- Full MTF bullish alignment (1M/5M/15M Strong Bullish)
- Trend stack: EMA, Supertrend, MACD, ADX all agree
- Defined risk: SL 1957.12 ≈ **12.20 pts (~0.62%)** below entry; RR to TP1 **1:1.2**
- Asian session widens stop via `ASIAN_WIDEN = 1.4` in `risk.py` (appropriate for thin liquidity)

Risk factors:

- **RSI 92.75** — statistically stretched; historically associated with short-term mean-reversion pressure on crypto alts and majors
- **Asian / Low Liquidity** — wider stops help, but slippage, wicks, and false breaks are more common
- **Buy-side liquidity sweep at highs** — can mark continuation **or** stop-run before reversal; not gated
- **Late-trend entry** — 10/11 confirmations with only RSI missing often indicates an **extended** move, not an early one

**Risk geometry check (from signal):**

| Level | Price | Distance from entry |
|---|---|---|
| Entry | 1969.32 | — |
| SL | 1957.12 | −12.20 (−0.62%) |
| TP1 | 1983.96 | +14.64 (+0.74%) |
| TP2 | 1993.72 | +24.40 |
| TP3 | 2005.92 | +36.60 |

The stop is structurally valid, but **location quality is poor**: entering at RSI 92.75 means buying near a local exhaustion zone, not at a pullback within trend.

**Statistical safety verdict:** Risk is **defined**, but **edge quality is weak** for immediate continuation.

---

## 6. Probability of immediate pullback

**High.**

Reasoning tied to this signal's profile:

1. **RSI > 90** — price has advanced aggressively; short-horizon pullback probability rises sharply vs RSI 55–65 entries
2. **Buy-side liquidity sweep** — highs were taken; post-sweep retracements are common before next leg (if any)
3. **All timeframes already Strong Bullish** — little room left for "easy" continuation; more two-way volatility
4. **Asian session** — lower participation; moves can reverse quickly on thin books
5. **No RSI confirmation in reasons** — the strategy itself did not validate momentum as "healthy"; only "not blocked"

Expected near-term paths (not predictions, scenario framing):

| Scenario | Likelihood | Bot impact |
|---|---|---|
| Shallow pullback toward VWAP / 5M mean | **Elevated** | May tag SL or BE if TP1 not reached first |
| Sideways chop 8-minute validity window | Moderate | Signal expires before resolution |
| Immediate continuation to TP1 | Possible but **lower quality edge** | Would require sustained momentum despite overbought |

---

## 7. Should an RSI hard filter exist?

**Yes — for this strategy's stated goals.**

Current design assumes RSI confirms **healthy momentum** (52–75). If RSI is outside that band, the strategy already implies the condition is **not healthy** — yet BUY is still allowed at 92.75.

A hard filter would align code with economic meaning:

| Proposed rule (conceptual — not implemented) | Rationale |
|---|---|
| **Block BUY if RSI > 75** (or 80) | Prevents chasing blow-off tops |
| **Block SELL if RSI < 25** (or 20) | Symmetric oversold protection |
| Optional: allow override only with ≥12/12 confirmations + London/NY | Keeps rare exception path |

Without a hard filter, `MIN_CONFIRMATIONS=9` makes it routine to buy **without** RSI approval — exactly what happened here.

---

## 8. Should RSI reduce AI Score instead of allowing BUY?

**It already reduces score — but insufficiently.**

Current behavior:

- Missing RSI → **−9 points** (82 → 73 before session penalty; 74 after)
- Missing RSI → **−1 confirmation** (11 → 10)

Problem: at extreme RSI (**92.75**), a linear −9 penalty is too small. The strategy treats "RSI 74" and "RSI 92.75" identically — both simply fail `rsi_bull`.

**Better performance-oriented approaches (recommendations only):**

| Approach | Effect |
|---|---|
| **Graduated RSI penalty** | e.g. RSI 75–80: −9; 80–85: −15; >85: −25 or hard block |
| **RSI as mandatory confirmation for BUY** | Require `rsi_bull` when `MIN_CONFIRMATIONS` logic fires |
| **Cap confidence when RSI fails** | Prevent 82% confidence display when a core momentum check failed |
| **Display RSI status** | Show `RSI : 92.75 ❌ (overbought — excluded)` in Telegram |

Score reduction alone **without** a ceiling or veto will continue to allow extreme overbought entries whenever trend factors stack strongly — as in this ETH case.

---

## 9. Recommendations (performance-only; no code changes made)

Only changes that would likely improve **real trading outcomes**:

### High impact

1. **Hard veto:** `BUY` blocked when `RSI > 75` (or configurable `RSI_BUY_MAX`); `SELL` blocked when `RSI < 25`.
2. **Mandatory RSI for tracked auto-signals:** require `rsi_bull`/`rsi_bear` in addition to `MIN_CONFIRMATIONS`, or raise effective bar to 10/11 when RSI fails.
3. **Confidence cap when RSI fails:** e.g. max 70% confidence if RSI outside band — prevents overconfidence on structurally weak entries.

### Medium impact

4. **Graduated overbought penalty** in score above 75 (not flat fail).
5. **Telegram clarity:** show RSI pass/fail like other indicators.
6. **Asian session:** consider blocking auto-BUY when `RSI > 70` and `session_active=False` (double thin-liquidity + extension risk).

### Low priority / do not change without retune

- Wilder RSI formula swap (audit DO NOT APPLY — would require band recalibration)
- Liquidity sweep as scored gate (currently info-only by design H-006)

---

## 10. Signal-to-code trace (checklist)

| Display field | Source | Matches signal? |
|---|---|---|
| AI Score 74 | weighted score − Asian penalty | ✅ |
| Grade B | 70 ≤ score < 80 | ✅ |
| Confidence 82% | 10 confirmations mapping | ✅ |
| Market Low Liquidity | `session_active=False` | ✅ |
| Session Asian ⚠️ | `get_current_session()` hour 0–8 UTC | ✅ |
| Tier Standard 75% | `position_sizing(10)` | ✅ |
| Buy confirmations 10 | sum of booleans minus RSI | ✅ |
| Sell confirmations 4 | bearish stack mostly false | ✅ |
| RSI not in Reasons | `rsi_bull=False` | ✅ |
| Liquidity sweep Buy Side | `high[-1] > max(high[-6:-1])` | ✅ |

---

## Final verdict

❌ **Strategy logic should be adjusted.**

**Why not "correct":**

The bot behaved **exactly as coded**, but issuing a **BUY at RSI 92.75** with **82% confidence** and **Standard Size (75%)** while the strategy's own RSI rule explicitly rejects that reading is a **material trading-logic mismatch**. RSI is labeled a momentum-health confirmation yet carries **no veto power** at extremes. For real performance, extreme overbought entries like this ETH signal should be blocked or heavily penalized — not promoted as a high-confidence standard-size long during Asian low liquidity.

**Operator note for this specific signal:** Treat as **low-quality late-trend chase**; elevated immediate pullback risk; size down or skip manually even though the bot posted BUY.

---

*End of report — no source code was modified.*
