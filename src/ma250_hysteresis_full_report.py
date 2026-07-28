"""Full corrected tearsheet for MA250 hysteresis (+4% entry, MA exit) at 2x."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M
from corrected_ma_regime_2x import BPY, load, regime, simulate, stats
from honest_best_2x_search import ma_hysteresis


def drawdown_details(ret):
    equity = (1 + ret).cumprod()
    dd = equity / equity.cummax() - 1
    trough = dd.idxmin()
    peak = equity.loc[:trough].idxmax()
    recovered = equity.loc[trough:][equity.loc[trough:] >= equity.loc[peak]]
    return peak, trough, None if recovered.empty else recovered.index[0], float(dd.min())


def spells(result):
    active = result.position > 0
    starts = active & ~active.shift(1, fill_value=False)
    ends = active & ~active.shift(-1, fill_value=False)
    rows = []
    for lo, hi in zip(active.index[starts], active.index[ends]):
        loc = result.returns.index.get_loc(lo)
        before = result.equity.iloc[loc - 1] if loc else 1.0
        rows.append((lo, hi, (hi - lo).total_seconds() / 86400,
                     result.equity.loc[hi] / before - 1))
    return rows


def main():
    df = load()
    signal = ma_hysteresis(df, ma=250, entry_buffer=.04, exit_buffer=0)
    h2 = simulate(df, signal, leverage=2)
    h1 = simulate(df, signal, leverage=1)
    h125 = simulate(df, signal, leverage=1.25)
    h150 = simulate(df, signal, leverage=1.5)
    h175 = simulate(df, signal, leverage=1.75)
    standard = simulate(df, regime(df, 250, 0), leverage=2)
    no_cost = simulate(df, signal, leverage=2, fee=0, slippage=0)
    no_funding = simulate(df, signal, leverage=2, charge_funding=False)
    bh = df["close"].pct_change().fillna(0)
    full = stats(h2)

    monthly = h2.returns.groupby([h2.returns.index.year, h2.returns.index.month]).apply(
        lambda r: M.total_return(r) * 100
    )
    trade_rows = spells(h2)
    trade_ret = pd.Series([x[3] for x in trade_rows])
    trade_days = pd.Series([x[2] for x in trade_rows])
    peak, trough, recovery, maxdd = drawdown_details(h2.returns)
    rolling = M.rolling_returns(h2.returns, BPY).dropna() * 100

    print("# MA250 hysteresis 2x — complete corrected results\n")
    print(f"Data: {df.index[0]} -> {df.index[-1]} ({len(df):,} four-hour bars)")
    print("Rule: enter when previous close > SMA250 × 1.04; exit when previous close < SMA250.")
    print("Execution: next bar open; fixed contracts until exit; 2x initial leverage.\n")

    print("## Headline\n")
    print("| Metric | Hysteresis 2x | Standard MA250 2x | Buy & hold |")
    print("|---|---:|---:|---:|")
    sb = {
        "net_%": M.total_return(bh)*100, "cagr_%": M.cagr(bh, BPY)*100,
        "sharpe": M.sharpe(bh, BPY), "maxdd_%": M.max_drawdown(bh)*100,
        "calmar": M.calmar(bh, BPY),
    }
    ss = stats(standard)
    for key, name, dec in [
        ("net_%", "Total return", 1), ("cagr_%", "CAGR", 2),
        ("sharpe", "Sharpe", 2), ("maxdd_%", "Maximum drawdown", 2),
        ("calmar", "Calmar", 2),
    ]:
        suffix = "%" if key in ("net_%", "cagr_%", "maxdd_%") else ""
        print(f"| {name} | {full[key]:,.{dec}f}{suffix} | "
              f"{ss[key]:,.{dec}f}{suffix} | {sb[key]:,.{dec}f}{suffix} |")
    print(f"| Sortino | {M.sortino(h2.returns, BPY):.2f} | "
          f"{M.sortino(standard.returns, BPY):.2f} | {M.sortino(bh, BPY):.2f} |")
    print(f"| Annual volatility | {M.ann_vol(h2.returns, BPY)*100:.2f}% | "
          f"{M.ann_vol(standard.returns, BPY)*100:.2f}% | {M.ann_vol(bh, BPY)*100:.2f}% |")
    print(f"| Rs 1 lakh becomes | Rs {100000*(1+full['net_%']/100):,.0f} | "
          f"Rs {100000*(1+ss['net_%']/100):,.0f} | Rs {100000*(1+sb['net_%']/100):,.0f} |")

    print("\n## Predetermined segments\n")
    print("| Segment | Total return | CAGR | Sharpe | Max DD | Calmar | Entries |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    segments = [
        ("Development", df.index[0], pd.Timestamp("2022-12-31 23:59:59", tz="UTC")),
        ("Validation", pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2024-12-31 23:59:59", tz="UTC")),
        ("Untouched holdout", pd.Timestamp("2025-01-01", tz="UTC"), df.index[-1]),
    ]
    for name, lo, hi in segments:
        result = simulate(df, signal, lo, hi, leverage=2)
        s = stats(result)
        print(f"| {name} | {s['net_%']:+.1f}% | {s['cagr_%']:+.2f}% | {s['sharpe']:.2f} | "
              f"{s['maxdd_%']:.1f}% | {s['calmar']:.2f} | {result.entries} |")

    print("\n## Year by year\n")
    print("| Year | Return | Sharpe | Max DD | In market | Avg effective leverage | B&H |")
    print("|---:|---:|---:|---:|---:|---:|---:|")
    for year in sorted(set(df.index.year)):
        mask = h2.returns.index.year == year
        r, p = h2.returns[mask], h2.position[mask]
        avglev = p[p > 0].mean() if (p > 0).any() else 0
        print(f"| {year} | {M.total_return(r)*100:+.1f}% | {M.sharpe(r,BPY):.2f} | "
              f"{M.max_drawdown(r)*100:.1f}% | {(p>0).mean()*100:.0f}% | "
              f"{avglev:.2f}x | {M.total_return(bh[mask])*100:+.1f}% |")

    print("\n## Leverage sensitivity — full period\n")
    print("| Initial leverage | CAGR | Sharpe | Max DD | Calmar |")
    print("|---:|---:|---:|---:|---:|")
    for lev, result in [(1,h1),(1.25,h125),(1.5,h150),(1.75,h175),(2,h2)]:
        s = stats(result)
        print(f"| {lev:.2f}x | {s['cagr_%']:.2f}% | {s['sharpe']:.2f} | "
              f"{s['maxdd_%']:.2f}% | {s['calmar']:.2f} |")

    print("\n## Trading, monthly behavior, and drawdown\n")
    print(f"- Entries: {h2.entries}; exits: {h2.exits}; open at end: {h2.position.iloc[-1] > 0}.")
    print(f"- Time in market: {(h2.position>0).mean()*100:.1f}%; average leverage while active: "
          f"{h2.position[h2.position>0].mean():.2f}x.")
    print(f"- Winning holding spells: {(trade_ret>0).mean()*100:.1f}% of {len(trade_ret)}.")
    print(f"- Median hold: {trade_days.median():.1f} days; longest: {trade_days.max():.1f} days.")
    print(f"- Best spell: {trade_ret.max()*100:+.1f}%; worst: {trade_ret.min()*100:+.1f}%.")
    print(f"- Positive months: {(monthly>0).mean()*100:.1f}%; negative: {(monthly<0).mean()*100:.1f}%.")
    print(f"- Best month: {monthly.idxmax()[0]}-{monthly.idxmax()[1]:02d}, {monthly.max():+.1f}%.")
    print(f"- Worst month: {monthly.idxmin()[0]}-{monthly.idxmin()[1]:02d}, {monthly.min():+.1f}%.")
    print(f"- Rolling 12m: worst {rolling.min():+.1f}%, median {rolling.median():+.1f}%, "
          f"best {rolling.max():+.1f}%.")
    print(f"- Worst drawdown: {maxdd*100:.2f}%; peak {peak}; trough {trough}; "
          f"recovery {recovery if recovery is not None else 'not recovered'}.")
    print(f"- Historical modeled liquidation: {h2.liquidated}; time: {h2.liquidation_time}.")

    print("\n## Cost sensitivity\n")
    print("| Model | Total return | CAGR |")
    print("|---|---:|---:|")
    for name, result in [("Base case",h2),("No fees/slippage",no_cost),("No funding",no_funding)]:
        s = stats(result)
        print(f"| {name} | {s['net_%']:,.1f}% | {s['cagr_%']:.2f}% |")


if __name__ == "__main__":
    main()
