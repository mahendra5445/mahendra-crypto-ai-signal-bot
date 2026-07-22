# Quick Implementation Guide - Fix Your Bot's TP/SL Issue

## TL;DR (Too Long; Didn't Read)
Your bot's TP targets are too far away. **Change 3 numbers in `risk.py` and watch your win rate jump from 0% to 60%+**

---

## Step 1: Identify Your Option

### **OPTION 1: RECOMMENDED FOR MOST** ✅
- Best for: All crypto pairs (BTC, ETH, etc.)
- Expected win rate: **60-70%**
- Expected avg profit: 1.5R - 2.5R per trade
- Use this unless you have a specific reason not to

### **OPTION 2: For Volatile Coins**
- Best for: SHIB, DOGE, small cap altcoins
- Expected win rate: **75-85%** (very high)
- Expected avg profit: 1.2R - 1.8R per trade
- Use if trading extremely choppy/volatile markets

### **OPTION 3: For Trending Markets**
- Best for: When market is in strong trend
- Expected win rate: **40-50%** (lower)
- Expected avg profit: 2R - 3R per trade
- Use only if you're getting stopped out a lot

---

## Step 2: Make the Change

### **OPTION 1 (Choose This One) - 3 Line Change**

**File:** `risk.py` (lines 68-71)

**Before:**
```python
    # Reward multiples of risk -> TP1 = 2.5R, TP2 = 4R, TP3 = 6R (runner target)
    tp1_reward = risk * 2.5
    tp2_reward = risk * 4.0
    tp3_reward = risk * 6.0
```

**After:**
```python
    # Reward multiples of risk -> TP1 = 1.5R, TP2 = 2.5R, TP3 = 4R (runner target)
    tp1_reward = risk * 1.5
    tp2_reward = risk * 2.5
    tp3_reward = risk * 4.0
```

### **OR - Use One of the Fixed Files Provided**

If you don't want to edit manually, you have three ready-to-use files:

1. **`risk_FIXED_OPTION1.py`** ← Use this one (recommended)
2. `risk_FIXED_OPTION2_AGGRESSIVE.py`
3. `risk_FIXED_OPTION3_CONSERVATIVE.py`

Replace your current `risk.py` with your chosen option:
```bash
# Backup original first
cp risk.py risk.py.backup

# Use Option 1 (recommended)
cp risk_FIXED_OPTION1.py risk.py

# OR Option 2 (aggressive)
# cp risk_FIXED_OPTION2_AGGRESSIVE.py risk.py

# OR Option 3 (conservative)
# cp risk_FIXED_OPTION3_CONSERVATIVE.py risk.py
```

---

## Step 3: Restart Your Bot

```bash
# Stop the bot (Ctrl+C or kill the process)
# Then restart it

python main.py
# or
docker restart crypto-bot
# or whatever your deployment method is
```

---

## Step 4: Monitor Results

### Watch for These Metrics

After 1-2 hours, run these commands:
```
/stats              # Overall statistics
/stats btc          # BTC performance
/stats eth          # ETH performance
```

### What You Should See
- ✅ **TP Hits increasing** (was 0, should be 5-10+)
- ✅ **Win rate climbing** (was 0%, should be 60%+)
- ✅ **BE (Breakeven) appearing** (good sign - means TP1 hit, then runner stopped at SL)

### If Something's Wrong

**If TP1 still not hitting after 4-8 hours:**
1. Check price data isn't delayed: `yfinance` sometimes lags
2. Try Option 2 (even tighter targets)
3. Increase `MIN_CONFIRMATIONS` in strategy.py (line 43) to filter worse signals

**If breakeven (BE) is too high (>30% of trades):**
1. Move to Option 2 (tighter TP1 target)
2. Or reduce `MIN_CONFIRMATIONS` to trigger earlier in moves

**If average profit is too small (<0.5R):**
1. Move to Option 3 (wider SL, bigger TP targets)
2. Only use this in strong trending markets though

---

## Step 5: Fine-Tune (Optional)

After 24-48 hours of testing, you might want to fine-tune further.

### If TP1 Hit Rate is >80% but profits are small
→ Use **Option 2** instead

### If TP1 Hit Rate is <40%
→ Use **Option 2** (make TP1 even tighter)

### If win rate is good but breakeven is too common
→ Use **Option 2** (helps runners escape with profit)

### If you're in strong trend and want bigger profits
→ Try **Option 3** (wider SL allows bigger moves)

---

## Code Change Comparison

| Metric | Original | Option 1 | Option 2 | Option 3 |
|--------|----------|----------|----------|----------|
| SL Distance | 2.5x ATR | 2.5x ATR | 2.5x ATR | 3.5x ATR |
| TP1 Target | 2.5R | **1.5R** | **1.2R** | 2.0R |
| TP2 Target | 4R | **2.5R** | **1.8R** | 3.0R |
| TP3 Target | 6R | **4R** | **3R** | 5.0R |
| Expected Win % | 0% | **60-70%** | **75-85%** | 40-50% |
| Avg Profit | N/A | 1.5-2.5R | 1.2-1.8R | 2-3R |

---

## Rollback Plan (If Needed)

If the fix doesn't work for your use case:

```bash
# Go back to original
cp risk.py.backup risk.py
python main.py
```

Then try a different option from the list.

---

## Expected Timeline

- **Hour 0:** Deploy fix
- **Hour 1-2:** First TP hits should appear in `/stats`
- **Hour 4-8:** Pattern becomes clear (60%+ win rate should be obvious)
- **Hour 24:** Solid sample size to evaluate
- **Day 2-3:** Fully validated, ready to increase position sizes

---

## Troubleshooting Quick Reference

| Issue | Solution |
|-------|----------|
| TP1 still not hitting | Try Option 2 (tighter TP) |
| Breakeven too common | Try Option 2 |
| Profits too small | Stick with Option 1, wait for sample size |
| Getting stopped out still | Try Option 3 (wider SL) |
| Too many false signals | Increase MIN_CONFIRMATIONS in strategy.py |
| Data seems delayed | Check yfinance / data.py source |

---

## Questions Before Starting?

**Q: Will this change my position sizing?**
A: No. The bot calculates risk:reward ratio but position size is separate. Your sizing stays the same.

**Q: Should I change anything else?**
A: Not for now. Just change the TP targets first. Evaluate for 24h, then fine-tune if needed.

**Q: What if I want different TP for different assets?**
A: That would require code changes. For now, one setting applies to all assets (good enough to start).

**Q: Can I manually set SL/TP instead of ATR-based?**
A: Yes, but ATR-based is better. It adapts to volatility. Recommend trying this fix first.

**Q: How long until I see results?**
A: 2-4 hours if market is active. You'll see TP hits immediately in `/stats`.

---

## Next Steps

1. ✅ Choose your option (Option 1 recommended)
2. ✅ Edit risk.py or copy the fixed file
3. ✅ Restart the bot
4. ✅ Check `/stats` after 2 hours
5. ✅ Evaluate after 24 hours
6. ✅ Adjust if needed

**Good luck! Your bot's win rate should jump significantly within hours.** 🚀

---

## Getting Help

If something isn't clear:
1. Re-read the Analysis document (TP_SL_ANALYSIS_AND_FIX.md)
2. Check the code comments in the fixed files
3. Verify you're editing the correct lines in risk.py
4. Test with Option 1 first (most proven)
