"""
Research runner: design + backtest + forward-test the candidate strategies and
compare them, honestly, against buy-and-hold.

Method (built to resist the usual backtest lies):
  1. Split history into IN-SAMPLE (train) and OUT-OF-SAMPLE (test) by date.
  2. Grid-search each strategy's parameters on TRAIN ONLY, maximizing Sharpe.
  3. Report those chosen params on TEST -- data the optimizer never saw.
  4. Run a WALK-FORWARD analysis: re-optimize on a rolling trailing window and
     trade the next window, stitching a true out-of-sample equity curve across the
     whole span. This is the number that matters.
  5. Everything is net of 0.10% fee + 5 bps slippage per side.

Usage:  python src/research.py
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_fetch import fetch_klines
from legacy_strategies import STRATEGIES, buy_and_hold
from backtest import run_backtest
import metrics as M

BARS_PER_YEAR = 24 * 365
TRAIN_END = "2023-01-01"          # train: 2019..2022  | test: 2023..now
MIN_EXPOSURE = 0.05               # reject degenerate "barely trades" params
REPORTS = Path(__file__).resolve().parent.parent / "reports"
REPORTS.mkdir(exist_ok=True)


def _param_combos(grid: dict):
    keys = list(grid)
    for values in itertools.product(*grid.values()):
        yield dict(zip(keys, values))


def optimize(strat, df: pd.DataFrame, objective=lambda r: M.sharpe(r.returns, BARS_PER_YEAR)):
    """Return (best_params, best_score) by grid search on the given df (in-sample)."""
    grid = getattr(strat, "param_grid", {})
    best_params, best_score = None, -np.inf
    for params in _param_combos(grid):
        target = strat(df, **params)
        res = run_backtest(df, target, bars_per_year=BARS_PER_YEAR)
        if res.gross_exposure_time < MIN_EXPOSURE:
            continue
        score = objective(res)
        if np.isfinite(score) and score > best_score:
            best_params, best_score = params, score
    return best_params or next(_param_combos(grid)), best_score


def evaluate(strat, df: pd.DataFrame, params: dict) -> tuple[dict, object]:
    target = strat(df, **params)
    res = run_backtest(df, target, bars_per_year=BARS_PER_YEAR)
    stats = M.summary(res.returns, BARS_PER_YEAR, res.trades, res.gross_exposure_time)
    return stats, res


# --------------------------------------------------------------------------- #
# Walk-forward: re-optimize on trailing window, trade the next window OOS
# --------------------------------------------------------------------------- #
def walk_forward(strat, df: pd.DataFrame, train_bars: int, test_bars: int) -> pd.Series:
    idx = df.index
    oos_returns = []
    start = 0
    while start + train_bars + test_bars <= len(df):
        train = df.iloc[start:start + train_bars]
        test = df.iloc[start + train_bars: start + train_bars + test_bars]
        params, _ = optimize(strat, train)
        # Build signal on train+test so indicators have warmup, then slice test.
        combined = df.iloc[start: start + train_bars + test_bars]
        target = strat(combined, **params)
        res = run_backtest(combined, target, bars_per_year=BARS_PER_YEAR)
        oos_returns.append(res.returns.loc[test.index])
        start += test_bars
    if not oos_returns:
        return pd.Series(dtype=float)
    return pd.concat(oos_returns)


def fmt(stats: dict) -> str:
    return (f"ret={stats['total_return']*100:8.1f}%  cagr={stats['cagr']*100:6.1f}%  "
            f"vol={stats['ann_vol']*100:5.1f}%  sharpe={stats['sharpe']:5.2f}  "
            f"sortino={stats['sortino']:5.2f}  maxDD={stats['max_drawdown']*100:6.1f}%  "
            f"calmar={stats['calmar']:5.2f}  expo={stats['exposure']*100:4.0f}%  "
            f"trades={stats['trades']:>4}")


def main():
    df = fetch_klines()
    df = df[~df.index.duplicated()].sort_index()
    train = df.loc[:TRAIN_END]
    test = df.loc[TRAIN_END:]
    print(f"\nData: {df.index[0]} -> {df.index[-1]}  ({len(df)} bars)")
    print(f"Train: {train.index[0].date()} -> {train.index[-1].date()}  ({len(train)} bars)")
    print(f"Test:  {test.index[0].date()} -> {test.index[-1].date()}  ({len(test)} bars)\n")

    # ---- Benchmark: buy & hold -------------------------------------------- #
    bh_train_stats, bh_train_res = evaluate(lambda d: buy_and_hold(d), train, {})
    bh_test_stats, bh_test_res = evaluate(lambda d: buy_and_hold(d), test, {})
    print("=" * 118)
    print(f"{'BUY & HOLD (benchmark)':<26} TRAIN  {fmt(bh_train_stats)}")
    print(f"{'':<26} TEST   {fmt(bh_test_stats)}")
    print("=" * 118)

    rows = []
    rows.append({"strategy": "buy_and_hold", "split": "train", **bh_train_stats, "params": ""})
    rows.append({"strategy": "buy_and_hold", "split": "test", **bh_test_stats, "params": ""})

    test_curves = {"buy_and_hold": bh_test_res.equity}
    wf_curves = {}
    wf_rows = []

    # 1 year train window, 3 month step for walk-forward
    WF_TRAIN = 24 * 365
    WF_TEST = 24 * 90

    for name, strat in STRATEGIES.items():
        params, train_score = optimize(strat, train)
        tr_stats, _ = evaluate(strat, train, params)
        te_stats, te_res = evaluate(strat, test, params)
        print(f"\n{name:<26} params={params}")
        print(f"{'':<26} TRAIN  {fmt(tr_stats)}")
        print(f"{'':<26} TEST   {fmt(te_stats)}")

        rows.append({"strategy": name, "split": "train", **tr_stats, "params": str(params)})
        rows.append({"strategy": name, "split": "test", **te_stats, "params": str(params)})
        test_curves[name] = te_res.equity

        # Walk-forward
        wf_ret = walk_forward(strat, df, WF_TRAIN, WF_TEST)
        if len(wf_ret):
            wf_stats = M.summary(wf_ret, BARS_PER_YEAR)
            wf_curves[name] = (1 + wf_ret).cumprod()
            wf_rows.append({"strategy": name, **wf_stats})
            print(f"{'':<26} WALKFWD{fmt(wf_stats)}")

    # buy&hold walk-forward equivalent = hold over the same OOS span
    if wf_curves:
        any_wf = next(iter(wf_curves.values()))
        bh_wf_ret = df["close"].pct_change().reindex(any_wf.index).fillna(0.0)
        bh_wf_stats = M.summary(bh_wf_ret, BARS_PER_YEAR)
        wf_curves["buy_and_hold"] = (1 + bh_wf_ret).cumprod()
        wf_rows.append({"strategy": "buy_and_hold", **bh_wf_stats})
        print(f"\n{'buy_and_hold':<26} WALKFWD{fmt(bh_wf_stats)}")

    # ---- Persist tables --------------------------------------------------- #
    pd.DataFrame(rows).to_csv(REPORTS / "split_results.csv", index=False)
    pd.DataFrame(wf_rows).to_csv(REPORTS / "walkforward_results.csv", index=False)
    print(f"\n[report] wrote reports/split_results.csv and reports/walkforward_results.csv")

    # ---- Plots ------------------------------------------------------------ #
    try:
        _plots(test_curves, wf_curves, df)
    except Exception as e:
        print(f"[plot] skipped ({e})")

    _write_markdown(rows, wf_rows)


def _plots(test_curves, wf_curves, df):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(13, 11))
    for name, eq in test_curves.items():
        axes[0].plot(eq.index, eq.values, label=name, lw=1.6 if name != "buy_and_hold" else 2.4,
                     alpha=0.9, color="black" if name == "buy_and_hold" else None)
    axes[0].set_yscale("log")
    axes[0].set_title("Out-of-sample TEST (2023 -> now): equity, log scale, net of fees")
    axes[0].legend(fontsize=8); axes[0].grid(True, alpha=0.3)

    for name, eq in wf_curves.items():
        axes[1].plot(eq.index, eq.values, label=name, lw=1.6 if name != "buy_and_hold" else 2.4,
                     alpha=0.9, color="black" if name == "buy_and_hold" else None)
    axes[1].set_yscale("log")
    axes[1].set_title("WALK-FORWARD (rolling re-optimization, fully out-of-sample)")
    axes[1].legend(fontsize=8); axes[1].grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(REPORTS / "equity_curves.png", dpi=120)
    print("[plot] wrote reports/equity_curves.png")


def _write_markdown(rows, wf_rows):
    df_split = pd.DataFrame(rows)
    df_wf = pd.DataFrame(wf_rows)
    cols = ["total_return", "cagr", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar", "exposure", "trades"]

    def table(d):
        d = d.copy()
        for c in ["total_return", "cagr", "ann_vol", "max_drawdown", "exposure"]:
            if c in d:
                d[c] = (d[c] * 100).round(1)
        for c in ["sharpe", "sortino", "calmar"]:
            if c in d:
                d[c] = d[c].round(2)
        return d.to_markdown(index=False)

    lines = ["# BTC/USDT Hourly Strategy Research\n",
             "All returns net of 0.10% fee + 5 bps slippage per side. Long/flat spot only.\n",
             "## Out-of-sample TEST (params chosen on 2019-2022 train, evaluated on 2023+)\n",
             table(df_split[df_split.split == "test"][["strategy"] + cols + ["params"]]), "",
             "## In-sample TRAIN (for overfit comparison)\n",
             table(df_split[df_split.split == "train"][["strategy"] + cols]), ""]
    if len(df_wf):
        lines += ["## Walk-forward (rolling re-optimization, the honest number)\n",
                  table(df_wf[["strategy"] + cols]), ""]
    (REPORTS / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("[report] wrote reports/REPORT.md")


if __name__ == "__main__":
    main()
