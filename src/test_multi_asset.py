"""
Does running trend_ensemble across MULTIPLE coins (not just BTC) improve results?

Idea: the same strategy traded independently on several assets gives uncorrelated
trend bets. Diversification should smooth the equity curve and lift risk-adjusted
return without adding any overfitting (it's the SAME strategy, same params).

Portfolio construction: equal-weight, daily-rebalanced across whichever assets are
listed at each bar. Each leg is long/flat per its OWN trend_ensemble signal. Every
leg's returns are already net of 0.10% fee + 5 bps slippage.

Usage:  python src/test_multi_asset.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_fetch import fetch_klines
from strategies_v2 import trend_ensemble
from backtest import run_backtest
import metrics as M

BPY = 365
FEE, SLIP = 0.001, 0.0005
ASSETS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT"]


def leg_returns(symbol):
    df = fetch_klines(symbol, "1d", "2017-08-01")
    df = df[~df.index.duplicated()].sort_index()
    sig = trend_ensemble(df, threshold=0.5)
    res = run_backtest(df, sig, fee=FEE, slippage=SLIP, bars_per_year=BPY)
    bh = df["close"].pct_change().fillna(0.0)
    return res.returns.rename(symbol), bh.rename(symbol)


def show(ret, tag):
    s = M.summary(ret, BPY)
    print(f"{tag:<34} ret={s['total_return']*100:9.1f}%  cagr={s['cagr']*100:6.1f}%  "
          f"sharpe={s['sharpe']:5.2f}  sortino={s['sortino']:5.2f}  "
          f"maxDD={s['max_drawdown']*100:6.1f}%  calmar={s['calmar']:5.2f}")
    return s


def main():
    strat_legs, bh_legs = {}, {}
    print("Per-asset trend_ensemble (standalone):")
    for a in ASSETS:
        sr, br = leg_returns(a)
        strat_legs[a] = sr
        bh_legs[a] = br
        # restrict each to its live history for fair standalone stats
        live = sr.loc[br.abs().cumsum() > 0]
        show(live, f"  {a}")

    strat = pd.DataFrame(strat_legs)
    bh = pd.DataFrame(bh_legs)
    # only count an asset once it actually trades (avoid pre-listing zeros)
    listed = bh.abs().cumsum() > 0
    strat = strat.where(listed)
    bh = bh.where(listed)

    # common window: from when BTC starts (earliest) to end
    start = "2018-06-01"
    strat, bh = strat.loc[start:], bh.loc[start:]

    port_strat = strat.mean(axis=1, skipna=True).fillna(0.0)
    port_bh = bh.mean(axis=1, skipna=True).fillna(0.0)
    btc_strat = strat["BTCUSDT"].fillna(0.0)
    btc_bh = bh["BTCUSDT"].fillna(0.0)

    print(f"\n{'='*94}")
    print("HEAD-TO-HEAD (common window from 2018-06):")
    print("="*94)
    show(btc_bh, "BTC buy & hold")
    show(btc_strat, "BTC trend_ensemble (single asset)")
    show(port_bh, "Equal-weight buy & hold (6 coins)")
    port = show(port_strat, "Equal-weight trend_ensemble (6 coins)")

    # correlation of the strategy legs (diversification evidence)
    print("\nCorrelation of per-asset STRATEGY daily returns (lower = more diversification):")
    corr = strat.dropna(how="all").corr()
    print(corr.round(2).to_string())
    avg_corr = corr.where(~np.eye(len(corr), dtype=bool)).stack().mean()
    print(f"\nAverage pairwise correlation: {avg_corr:.2f}")

    _plot(btc_bh, btc_strat, port_strat)
    print("\nTakeaway metric -> portfolio Sharpe %.2f vs BTC-only Sharpe %.2f"
          % (port["sharpe"], M.sharpe(btc_strat, BPY)))


def _plot(btc_bh, btc_strat, port_strat):
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        REPORTS = Path(__file__).resolve().parent.parent / "reports"
        fig, ax = plt.subplots(figsize=(13, 7))
        ax.plot((1+btc_bh).cumprod(), label="BTC buy & hold", color="black", lw=2)
        ax.plot((1+btc_strat).cumprod(), label="BTC trend_ensemble", color="#2563eb", lw=1.6)
        ax.plot((1+port_strat).cumprod(), label="6-coin trend_ensemble portfolio", color="#16a34a", lw=2)
        ax.set_yscale("log"); ax.set_title("Multi-asset trend_ensemble vs BTC-only, net of costs")
        ax.legend(); ax.grid(alpha=0.3)
        fig.tight_layout(); fig.savefig(REPORTS / "multi_asset.png", dpi=120)
        print("[plot] wrote reports/multi_asset.png")
    except Exception as e:
        print(f"[plot] skipped ({e})")


if __name__ == "__main__":
    main()
