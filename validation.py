"""
validation.py
Walk-Forward Optimization + Monte Carlo Backtesting.

Both operate on top of main.py's simulate_bars() (the actual trade
simulation loop, factored out of run_backtest so it can be re-run on
arbitrary bar ranges without duplicating the signal logic).

Walk-forward:
  Splits the full history into N rolling (train, test) folds. Weights
  (scoring.WeightStore) are allowed to adapt on the TRAIN slice of each
  fold; they are then FROZEN and the fold's TEST slice is run out-of-sample
  with no further learning. Only test-slice trades are pooled into the
  final report, so the headline numbers reflect out-of-sample performance,
  not in-sample curve-fitting.

  Feature computation (indicators/SMC/etc.) is done ONCE on the full
  dataframe before folding — that is NOT lookahead, because every one of
  those features is causal (rolling windows only look backward). Folding
  only changes which bar range the trade-simulation loop and weight
  updates are allowed to see, not what data went into computing a feature
  at a given bar.

Monte Carlo:
  Bootstrap-resamples the realized per-trade R-multiples (with replacement)
  thousands of times to build a distribution of possible equity curves.
  This answers "how much of the reported edge is one lucky/unlucky
  sequence of results?" — gives percentile equity and drawdown ranges and
  a probability of ending in the red, instead of a single point estimate.
"""
import numpy as np
import pandas as pd


def walk_forward_windows(n_bars: int, n_folds: int = 5, train_frac: float = 0.7,
                          warmup: int = 60):
    """
    Yield (train_start, train_end, test_start, test_end) index tuples,
    rolling forward through the dataset. `warmup` bars at the very start
    are skipped (matches main.py's indicator warmup before signals begin).
    """
    usable = n_bars - warmup
    fold_size = usable // n_folds
    windows = []
    for k in range(n_folds):
        start = warmup + k * fold_size
        end = start + fold_size if k < n_folds - 1 else n_bars
        train_end = start + int((end - start) * train_frac)
        if train_end <= start or end <= train_end:
            continue
        windows.append((start, train_end, train_end, end))
    return windows


def run_walk_forward(df, feats, structure_df, session_df, tf30_trend, tf1h_trend,
                      event_flags, weight_store_cls, risk_cfg, trade_cfg,
                      simulate_fn, n_folds: int = 5, train_frac: float = 0.7,
                      warmup: int = 60):
    """
    Orchestrates walk-forward validation using main.simulate_bars as
    `simulate_fn`. Returns (test_trades_df, per_fold_summary).

    weight_store_cls: a zero-arg callable returning a fresh in-memory
    WeightStore-like object per fold (so folds don't contaminate each
    other's learned weights) — see main.py's `fresh_weight_store()`.
    """
    windows = walk_forward_windows(len(df), n_folds=n_folds, train_frac=train_frac, warmup=warmup)
    all_test_trades = []
    fold_summaries = []

    for fold_i, (tr_s, tr_e, te_s, te_e) in enumerate(windows):
        weight_store = weight_store_cls()

        # TRAIN: weights adapt (learn=True) on this slice, in-sample.
        simulate_fn(df, feats, structure_df, session_df, tf30_trend, tf1h_trend,
                    event_flags, weight_store, risk_cfg, trade_cfg,
                    start_idx=tr_s, end_idx=tr_e, learn=True)

        # TEST: weights are now frozen (learn=False) — genuine out-of-sample.
        test_trades = simulate_fn(df, feats, structure_df, session_df, tf30_trend, tf1h_trend,
                                   event_flags, weight_store, risk_cfg, trade_cfg,
                                   start_idx=te_s, end_idx=te_e, learn=False)

        if test_trades is not None and not test_trades.empty:
            test_trades = test_trades.copy()
            test_trades["fold"] = fold_i
            all_test_trades.append(test_trades)

        n = len(test_trades) if test_trades is not None else 0
        wr = round((test_trades["pnl_r"] > 0).mean() * 100, 2) if n else 0.0
        total_r = round(test_trades["pnl_r"].sum(), 2) if n else 0.0
        fold_summaries.append({
            "fold": fold_i, "train_bars": tr_e - tr_s, "test_bars": te_e - te_s,
            "test_trades": n, "test_win_rate_pct": wr, "test_total_r": total_r,
        })

    combined = pd.concat(all_test_trades, ignore_index=True) if all_test_trades else pd.DataFrame()
    return combined, fold_summaries


def monte_carlo_backtest(pnl_r: pd.Series, n_sims: int = 5000, seed: int = 42) -> dict:
    """
    Bootstrap resample (with replacement) of the realized trade R-multiples.
    Each simulation reorders/resamples the SAME trades to build an
    alternative equity path — this tests sequence risk, not whether the
    trades themselves were real.
    """
    rng = np.random.default_rng(seed)
    r = pd.Series(pnl_r).dropna().values
    n = len(r)
    if n == 0:
        return {"error": "no trades to simulate"}

    final_equities = np.empty(n_sims)
    max_drawdowns = np.empty(n_sims)

    for s in range(n_sims):
        sample = rng.choice(r, size=n, replace=True)
        equity = np.cumsum(sample)
        running_max = np.maximum.accumulate(equity)
        dd = equity - running_max
        final_equities[s] = equity[-1]
        max_drawdowns[s] = dd.min()

    return {
        "n_sims": n_sims,
        "n_trades_per_sim": n,
        "actual_total_r": round(float(r.sum()), 2),
        "final_equity_r": {
            "mean": round(float(final_equities.mean()), 2),
            "median": round(float(np.median(final_equities)), 2),
            "p5": round(float(np.percentile(final_equities, 5)), 2),
            "p95": round(float(np.percentile(final_equities, 95)), 2),
        },
        "max_drawdown_r": {
            "mean": round(float(max_drawdowns.mean()), 2),
            "worst_case_p5": round(float(np.percentile(max_drawdowns, 5)), 2),
            "median": round(float(np.median(max_drawdowns)), 2),
        },
        "prob_end_negative_pct": round(float((final_equities < 0).mean() * 100), 2),
        "prob_drawdown_exceeds_10r_pct": round(float((max_drawdowns < -10).mean() * 100), 2),
    }
