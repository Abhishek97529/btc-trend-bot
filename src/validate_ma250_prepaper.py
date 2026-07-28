"""Pre-paper validation battery for frozen standard MA250, BTCUSDT 4h, 2x."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M
from corrected_ma_regime_2x import BPY, load, regime, simulate, stats

SEED = 250


def row(label, result):
    s = stats(result)
    return (label, s["cagr_%"], s["sharpe"], s["maxdd_%"], s["calmar"],
            result.entries, result.liquidated)


def print_table(title, rows):
    print(f"\n## {title}")
    print(f"{'test':<34}{'CAGR%':>9}{'Sharpe':>9}{'maxDD%':>10}{'Calmar':>9}"
          f"{'entries':>9}{'liq':>6}")
    print("-" * 86)
    for label, ca, sh, dd, cal, ent, liq in rows:
        print(f"{label:<34}{ca:>9.2f}{sh:>9.2f}{dd:>10.1f}{cal:>9.2f}{ent:>9}{str(liq):>6}")


def trade_spells(result):
    active = result.position > 0
    starts = active & ~active.shift(1, fill_value=False)
    ends = active & ~active.shift(-1, fill_value=False)
    returns = []
    for lo, hi in zip(active.index[starts], active.index[ends]):
        loc = result.equity.index.get_loc(lo)
        before = result.equity.iloc[loc - 1] if loc else 1.0
        returns.append(float(result.equity.loc[hi] / before - 1))
    return np.array(returns)


def fixed_fold_test(df, signal, train_months, test_months):
    """Fixed rule; train length only sets OOS start. Every test fold starts flat."""
    train_bars = int(train_months / 12 * BPY)
    test_bars = int(test_months / 12 * BPY)
    cursor = train_bars
    parts = []
    liquidated = False
    entries = 0
    while cursor < len(df):
        end = min(cursor + test_bars, len(df))
        result = simulate(df, signal, df.index[cursor], df.index[end - 1])
        parts.append(result.returns)
        entries += result.entries
        liquidated |= result.liquidated
        cursor = end
    ret = pd.concat(parts)
    s = {
        "cagr_%": M.cagr(ret, BPY)*100, "sharpe": M.sharpe(ret, BPY),
        "maxdd_%": M.max_drawdown(ret)*100, "calmar": M.calmar(ret, BPY),
    }
    return (f"{train_months}m start / {test_months}m folds", s["cagr_%"], s["sharpe"],
            s["maxdd_%"], s["calmar"], entries, liquidated)


def block_bootstrap(ret, n=2000, block=42):
    """One-week block bootstrap of the complete 4h return history."""
    values = ret.to_numpy()
    length = len(values)
    rng = np.random.default_rng(SEED)
    totals, dds = np.empty(n), np.empty(n)
    max_start = length - block
    for i in range(n):
        starts = rng.integers(0, max_start + 1, size=int(np.ceil(length/block)))
        sample = np.concatenate([values[s:s+block] for s in starts])[:length]
        eq = np.cumprod(1 + sample)
        totals[i] = eq[-1] - 1
        dds[i] = np.min(eq / np.maximum.accumulate(eq) - 1)
    return totals, dds


def main():
    df = load()
    base_signal = regime(df, 250, 0)
    base = simulate(df, base_signal)
    base_stats = stats(base)
    print("# Frozen MA250 2x pre-paper validation")
    print(f"\nData {df.index[0]} -> {df.index[-1]} | {len(df):,} 4h bars")
    print(f"Baseline CAGR {base_stats['cagr_%']:.2f}%, Sharpe {base_stats['sharpe']:.2f}, "
          f"maxDD {base_stats['maxdd_%']:.2f}%")

    sensitivity = []
    for ma in (150,175,200,225,250,275,300,325,350,400):
        sensitivity.append(row(f"MA{ma}", simulate(df, regime(df,ma,0))))
    print_table("Parameter sensitivity", sensitivity)

    delays = []
    for extra in (0,1,2,3):
        delayed = base_signal.shift(extra).fillna(0)
        delays.append(row(f"additional delay {extra} bars", simulate(df, delayed)))
    print_table("Execution-delay stress", delays)

    costs = []
    for total_cost in (.0004,.0007,.0010,.0015,.0025):
        costs.append(row(f"{total_cost*100:.02f}% per side",
                         simulate(df,base_signal,fee=total_cost,slippage=0)))
    print_table("Trading-cost stress", costs)

    funding = []
    for mult in (0,1,1.25,1.5,2):
        funding.append(row(f"historical funding x{mult:g}",
                           simulate(df,base_signal,funding_multiplier=mult)))
    print_table("Funding stress", funding)

    leverage = []
    for lev in (1,1.25,1.5,1.75,2):
        leverage.append(row(f"{lev:.2f}x", simulate(df,base_signal,leverage=lev)))
    print_table("Leverage sensitivity", leverage)

    folds = [fixed_fold_test(df,base_signal,tr,te)
             for tr,te in ((12,3),(18,6),(24,6),(36,12))]
    print_table("Fixed-rule walk-forward/reset schedules", folds)

    shocks = []
    for shock in (.10,.20,.30,.40,.50):
        shocks.append(row(f"{shock:.0%} mark shock while active",
                          simulate(df,base_signal,mark_shock=shock)))
    print_table("Adverse mark-price shock stress", shocks)

    print("\n## Year and trade concentration")
    log_growth = np.log1p(base.returns).groupby(base.returns.index.year).sum()
    positive = log_growth[log_growth > 0]
    top_year_share = float(positive.max()/positive.sum()) if len(positive) else np.nan
    spell_ret = trade_spells(base)
    positive_trades = np.log1p(spell_ret[spell_ret > -1])
    ranked = np.sort(positive_trades)[::-1]
    top3_share = float(ranked[:3].sum()/ranked[ranked>0].sum())
    print(f"Positive-year log growth from best year: {top_year_share*100:.1f}%")
    print(f"Positive-trade log growth from best 3 spells: {top3_share*100:.1f}%")
    print(f"Trades/spells: {len(spell_ret)}; win rate: {(spell_ret>0).mean()*100:.1f}%; "
          f"median: {np.median(spell_ret)*100:+.2f}%")

    totals, dds = block_bootstrap(base.returns)
    print("\n## One-week block bootstrap (2,000 paths)")
    print(f"Probability terminal loss: {(totals<0).mean()*100:.1f}%")
    print(f"Terminal return percentiles 5/50/95: "
          f"{np.percentile(totals,[5,50,95])[0]*100:,.1f}% / "
          f"{np.percentile(totals,[5,50,95])[1]*100:,.1f}% / "
          f"{np.percentile(totals,[5,50,95])[2]*100:,.1f}%")
    print(f"MaxDD percentiles 5/50/95: "
          f"{np.percentile(dds,[5,50,95])[0]*100:.1f}% / "
          f"{np.percentile(dds,[5,50,95])[1]*100:.1f}% / "
          f"{np.percentile(dds,[5,50,95])[2]*100:.1f}%")
    print(f"Probability DD worse than 50%: {(dds<-.50).mean()*100:.1f}%")
    print(f"Probability DD worse than 70%: {(dds<-.70).mean()*100:.1f}%")

    # Mechanical gates fixed before interpreting output.
    neighbors = [x for x in sensitivity if 200 <= int(x[0][2:]) <= 300]
    gates = {
        "MA200-300 all positive Sharpe": all(x[2] > 0 for x in neighbors),
        "1-bar extra delay remains positive": delays[1][2] > 0,
        "0.15%/side stress remains positive": costs[3][2] > 0,
        "1.5x funding stress remains positive": funding[3][2] > 0,
        "all fixed-rule schedules Sharpe > 0.6": all(x[2] > .6 for x in folds),
        "no historical liquidation": not base.liquidated,
        "best year <= 50% positive log growth": top_year_share <= .50,
        "bootstrap P(DD worse 70%) <= 25%": (dds<-.70).mean() <= .25,
    }
    print("\n## Pre-paper gates")
    for name, passed in gates.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    print(f"\nOVERALL: {sum(gates.values())}/{len(gates)} gates passed")


if __name__ == "__main__":
    main()
