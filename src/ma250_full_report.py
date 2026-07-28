"""Full reproducible tearsheet for the corrected BTCUSDT MA250 2x strategy."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M
from corrected_ma_regime_2x import BPY, load, regime, simulate, stats

def fmt(x, digits=1):
    return f"{x:,.{digits}f}"


def dd_details(ret):
    eq = (1 + ret).cumprod()
    dd = eq / eq.cummax() - 1
    trough = dd.idxmin()
    peak = eq.loc[:trough].idxmax()
    recovered = eq.loc[trough:][eq.loc[trough:] >= eq.loc[peak]]
    recovery = None if recovered.empty else recovered.index[0]
    return peak, trough, recovery, float(dd.min())


def annual_row(ret, pos, year):
    r = ret[ret.index.year == year]
    p = pos.loc[r.index]
    return (
        M.total_return(r) * 100,
        M.sharpe(r, BPY),
        M.max_drawdown(r) * 100,
        float((p > 0).mean()) * 100,
        float(p[p > 0].mean()) if (p > 0).any() else 0.0,
    )


def trade_spells(result):
    active = result.position > 0
    starts = active & ~active.shift(1, fill_value=False)
    ends = active & ~active.shift(-1, fill_value=False)
    start_idx, end_idx = list(active.index[starts]), list(active.index[ends])
    rows = []
    for lo, hi in zip(start_idx, end_idx):
        loc = result.returns.index.get_loc(lo)
        before = result.equity.iloc[loc - 1] if loc else 1.0
        after = result.equity.loc[hi]
        rows.append((lo, hi, (hi - lo).total_seconds() / 86400, after / before - 1))
    return rows


def main():
    df = load()
    signal = regime(df, 250, 0.0)
    x2 = simulate(df, signal, leverage=2.0)
    x1 = simulate(df, signal, leverage=1.0)
    no_cost = simulate(df, signal, leverage=2.0, fee=0.0, slippage=0.0)
    no_funding = simulate(df, signal, leverage=2.0, charge_funding=False)
    bh = df["close"].pct_change().fillna(0.0)

    s2, s1 = stats(x2), stats(x1)
    sb = {
        "net_%": M.total_return(bh) * 100,
        "cagr_%": M.cagr(bh, BPY) * 100,
        "sharpe": M.sharpe(bh, BPY),
        "maxdd_%": M.max_drawdown(bh) * 100,
        "calmar": M.calmar(bh, BPY),
    }
    peak, trough, recovery, mdd = dd_details(x2.returns)
    monthly = x2.returns.groupby([x2.returns.index.year, x2.returns.index.month]).apply(
        lambda r: M.total_return(r) * 100
    )
    best_month, worst_month = monthly.idxmax(), monthly.idxmin()
    exposure = float((x2.position > 0).mean()) * 100
    avg_lev = float(x2.position[x2.position > 0].mean())
    ann_vol = M.ann_vol(x2.returns, BPY) * 100
    sortino = M.sortino(x2.returns, BPY)
    spells = trade_spells(x2)
    spell_returns = pd.Series([x[3] for x in spells])
    spell_days = pd.Series([x[2] for x in spells])
    rolling_12m = M.rolling_returns(x2.returns, BPY).dropna() * 100

    # Same fixed-rule OOS window used by the 24m/6m optimizer.
    oos_start = df.index[2 * BPY]
    oos = simulate(df, signal, oos_start, df.index[-1], leverage=2.0)
    so = stats(oos)

    lines = [
        "# MA250 2× — corrected full report",
        "",
        f"Data: Binance BTCUSDT perpetual, 4-hour, {df.index[0]} → {df.index[-1]}.",
        "Signal: long 2× when the previous close is above its 250-bar SMA; otherwise flat.",
        "Execution: next bar open. Stateful contract quantity, real cached funding, "
        "0.04% fee + 0.03% slippage per side, 0.5% maintenance margin.",
        "",
        "## Headline",
        "",
        "| Metric | MA250 2× | MA250 1× perp | Buy & hold |",
        "|---|---:|---:|---:|",
        f"| Total return | {fmt(s2['net_%'])}% | {fmt(s1['net_%'])}% | {fmt(sb['net_%'])}% |",
        f"| CAGR | {fmt(s2['cagr_%'],2)}% | {fmt(s1['cagr_%'],2)}% | {fmt(sb['cagr_%'],2)}% |",
        f"| Sharpe | {fmt(s2['sharpe'],2)} | {fmt(s1['sharpe'],2)} | {fmt(sb['sharpe'],2)} |",
        f"| Sortino | {sortino:.2f} | {M.sortino(x1.returns, BPY):.2f} | {M.sortino(bh, BPY):.2f} |",
        f"| Annualized volatility | {ann_vol:.2f}% | {M.ann_vol(x1.returns, BPY)*100:.2f}% | "
        f"{M.ann_vol(bh, BPY)*100:.2f}% |",
        f"| Max drawdown | {fmt(s2['maxdd_%'],2)}% | {fmt(s1['maxdd_%'],2)}% | {fmt(sb['maxdd_%'],2)}% |",
        f"| Calmar | {fmt(s2['calmar'],2)} | {fmt(s1['calmar'],2)} | {fmt(sb['calmar'],2)} |",
        f"| ₹1 lakh becomes | ₹{100000*(1+s2['net_%']/100):,.0f} | "
        f"₹{100000*(1+s1['net_%']/100):,.0f} | ₹{100000*(1+sb['net_%']/100):,.0f} |",
        "",
        "## Operations and risk",
        "",
        f"- Entries: {x2.entries}; exits: {x2.exits}; open at end: {'yes' if x2.position.iloc[-1] > 0 else 'no'}.",
        f"- Time in market: {exposure:.1f}%; average effective leverage while invested: {avg_lev:.2f}×.",
        f"- Liquidation: {'yes, '+str(x2.liquidation_time) if x2.liquidated else 'none in historical simulation'}.",
        f"- Worst drawdown: {mdd*100:.2f}%, peak {peak}, trough {trough}, "
        f"recovery {recovery if recovery is not None else 'not recovered'}.",
        f"- Positive months: {(monthly > 0).mean()*100:.1f}%; negative months: {(monthly < 0).mean()*100:.1f}%.",
        f"- Best month: {best_month[0]}-{best_month[1]:02d}, {monthly.loc[best_month]:+.1f}%.",
        f"- Worst month: {worst_month[0]}-{worst_month[1]:02d}, {monthly.loc[worst_month]:+.1f}%.",
        f"- Completed holding spells: {len(spells)}; winning spells: {(spell_returns > 0).mean()*100:.1f}%.",
        f"- Median spell: {spell_days.median():.1f} days; longest: {spell_days.max():.1f} days.",
        f"- Best spell: {spell_returns.max()*100:+.1f}%; worst spell: {spell_returns.min()*100:+.1f}%.",
        f"- Rolling 12-month return: worst {rolling_12m.min():+.1f}%, median "
        f"{rolling_12m.median():+.1f}%, best {rolling_12m.max():+.1f}%.",
        "",
        "## Cost sensitivity",
        "",
        "| Model | Total return | CAGR |",
        "|---|---:|---:|",
        f"| Corrected base case | {s2['net_%']:,.1f}% | {s2['cagr_%']:.2f}% |",
        f"| No trading fee/slippage | {stats(no_cost)['net_%']:,.1f}% | {stats(no_cost)['cagr_%']:.2f}% |",
        f"| No funding | {stats(no_funding)['net_%']:,.1f}% | {stats(no_funding)['cagr_%']:.2f}% |",
        "",
        "## Fixed-rule out-of-sample-style window",
        "",
        f"From {oos.returns.index[0]} through {oos.returns.index[-1]}: total "
        f"{so['net_%']:.1f}%, CAGR {so['cagr_%']:.2f}%, Sharpe {so['sharpe']:.2f}, "
        f"max drawdown {so['maxdd_%']:.2f}%.",
        "",
        "## Year by year",
        "",
        "| Year | 2× return | 2× Sharpe | 2× maxDD | 1× return | B&H return | In market | Avg leverage |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for year in sorted(set(df.index.year)):
        r2, sh, dd, exp, lev = annual_row(x2.returns, x2.position, year)
        r1 = M.total_return(x1.returns[x1.returns.index.year == year]) * 100
        rb = M.total_return(bh[bh.index.year == year]) * 100
        lines.append(
            f"| {year} | {r2:+.1f}% | {sh:.2f} | {dd:.1f}% | {r1:+.1f}% | "
            f"{rb:+.1f}% | {exp:.0f}% | {lev:.2f}× |"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "The full-history result is in-sample and includes the parameter-selection era. "
        "The fixed-rule later window is the more credible historical reference. "
        "No historical liquidation is not a guarantee: this simulator does not reproduce "
        "every Binance tier, latency event, spread spike, ADL rule, or exchange failure.",
        "",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
