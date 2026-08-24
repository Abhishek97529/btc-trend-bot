"""
Round-2 research on DAILY bars: ensembles, vol-targeting, regime filter, and a
long/short futures variant. Same anti-overfit discipline as research.py:
in-sample tuning -> out-of-sample test -> walk-forward, all net of costs.

Usage:  python src/research_v2.py
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_fetch import fetch_klines
from strategies_v2 import STRATEGIES_LONG, STRATEGIES_LS
from legacy_strategies import buy_and_hold
from backtest import run_backtest
import metrics as M

BPY = 365
TRAIN_END = "2023-01-01"
MIN_EXPOSURE = 0.05
FUNDING_PER_DAY = 0.0003   # ~0.01%/8h funding assumption for the LS futures variant
REPORTS = Path(__file__).resolve().parent.parent / "reports"
REPORTS.mkdir(exist_ok=True)


def _combos(grid):
    keys = list(grid)
    for vals in itertools.product(*grid.values()):
        yield dict(zip(keys, vals))


def _bt(df, target, short=False):
    return run_backtest(df, target, bars_per_year=BPY, allow_short=short,
                        holding_cost=FUNDING_PER_DAY if short else 0.0)


def optimize(strat, df, short=False):
    best_p, best_s = None, -np.inf
    for p in _combos(getattr(strat, "param_grid", {})):
        res = _bt(df, strat(df, **p), short)
        if res.gross_exposure_time < MIN_EXPOSURE:
            continue
        s = M.sharpe(res.returns, BPY)
        if np.isfinite(s) and s > best_s:
            best_p, best_s = p, s
    return best_p or next(_combos(strat.param_grid)), best_s


def evaluate(strat, df, params, short=False):
    res = _bt(df, strat(df, **params), short)
    return M.summary(res.returns, BPY, res.trades, res.gross_exposure_time), res


def walk_forward(strat, df, train_bars, test_bars, short=False):
    oos = []
    start = 0
    while start + train_bars + test_bars <= len(df):
        train = df.iloc[start:start + train_bars]
        test = df.iloc[start + train_bars:start + train_bars + test_bars]
        p, _ = optimize(strat, train, short)
        combined = df.iloc[start:start + train_bars + test_bars]
        res = _bt(combined, strat(combined, **p), short)
        oos.append(res.returns.loc[test.index])
        start += test_bars
    return pd.concat(oos) if oos else pd.Series(dtype=float)


def fmt(s):
    return (f"ret={s['total_return']*100:8.1f}%  cagr={s['cagr']*100:6.1f}%  "
            f"vol={s['ann_vol']*100:5.1f}%  sharpe={s['sharpe']:5.2f}  "
            f"maxDD={s['max_drawdown']*100:6.1f}%  calmar={s['calmar']:5.2f}  "
            f"expo={s['exposure']*100:4.0f}%  trades={s['trades']:>4}")


def main():
    df = fetch_klines("BTCUSDT", "1d", "2017-08-01")
    df = df[~df.index.duplicated()].sort_index()
    train, test = df.loc[:TRAIN_END], df.loc[TRAIN_END:]
    print(f"\nDAILY  {df.index[0].date()} -> {df.index[-1].date()}  ({len(df)} bars)")
    print(f"Train {train.index[0].date()}->{train.index[-1].date()} ({len(train)})  "
          f"Test {test.index[0].date()}->{test.index[-1].date()} ({len(test)})\n")

    bh_tr, _ = evaluate(lambda d: buy_and_hold(d), train, {})
    bh_te, bh_res = evaluate(lambda d: buy_and_hold(d), test, {})
    print("=" * 112)
    print(f"{'BUY & HOLD':<22} TRAIN  {fmt(bh_tr)}")
    print(f"{'':<22} TEST   {fmt(bh_te)}")
    print("=" * 112)

    rows = [{"strategy": "buy_and_hold", "split": "test", **bh_te, "params": ""}]
    curves = {"buy_and_hold": bh_res.equity}
    wf_curves, wf_rows = {}, []
    WF_TRAIN, WF_TEST = 365 * 2, 90   # 2y train, 3m step

    def process(name, strat, short):
        p, _ = optimize(strat, train, short)
        tr, _ = evaluate(strat, train, p, short)
        te, te_res = evaluate(strat, test, p, short)
        print(f"\n{name:<22} params={p}")
        print(f"{'':<22} TRAIN  {fmt(tr)}")
        print(f"{'':<22} TEST   {fmt(te)}")
        rows.append({"strategy": name, "split": "test", **te, "params": str(p)})
        curves[name] = te_res.equity
        wf = walk_forward(strat, df, WF_TRAIN, WF_TEST, short)
        if len(wf):
            ws = M.summary(wf, BPY)
            wf_curves[name] = (1 + wf).cumprod()
            wf_rows.append({"strategy": name, **ws})
            print(f"{'':<22} WALKFWD{fmt(ws)}")

    for name, strat in STRATEGIES_LONG.items():
        process(name, strat, short=False)
    for name, strat in STRATEGIES_LS.items():
        process(name, strat, short=True)

    if wf_curves:
        span = next(iter(wf_curves.values())).index
        bh_wf = df["close"].pct_change().reindex(span).fillna(0.0)
        ws = M.summary(bh_wf, BPY)
        wf_curves["buy_and_hold"] = (1 + bh_wf).cumprod()
        wf_rows.append({"strategy": "buy_and_hold", **ws})
        print(f"\n{'buy_and_hold':<22} WALKFWD{fmt(ws)}")

    pd.DataFrame(rows).to_csv(REPORTS / "v2_test_results.csv", index=False)
    pd.DataFrame(wf_rows).to_csv(REPORTS / "v2_walkforward_results.csv", index=False)

    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(2, 1, figsize=(13, 11))
        for n, eq in curves.items():
            ax[0].plot(eq.index, eq.values, label=n, lw=2.4 if n == "buy_and_hold" else 1.6,
                       color="black" if n == "buy_and_hold" else None)
        ax[0].set_yscale("log"); ax[0].set_title("DAILY out-of-sample TEST (2023->now), net of costs")
        ax[0].legend(fontsize=8); ax[0].grid(alpha=0.3)
        for n, eq in wf_curves.items():
            ax[1].plot(eq.index, eq.values, label=n, lw=2.4 if n == "buy_and_hold" else 1.6,
                       color="black" if n == "buy_and_hold" else None)
        ax[1].set_yscale("log"); ax[1].set_title("DAILY walk-forward (fully out-of-sample)")
        ax[1].legend(fontsize=8); ax[1].grid(alpha=0.3)
        fig.tight_layout(); fig.savefig(REPORTS / "v2_equity_curves.png", dpi=120)
        print("\n[plot] wrote reports/v2_equity_curves.png")
    except Exception as e:
        print(f"[plot] skipped ({e})")


if __name__ == "__main__":
    main()
