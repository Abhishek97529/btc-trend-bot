"""
ONE-CYCLE returns for the LOCKED SPOT strategy vs buy & hold.

A Bitcoin "cycle" ~ trough-to-trough (roughly the 4-year halving rhythm). We measure
each complete cycle in our data, plus the current (incomplete) one, and also a plain
rolling 4-year (1460-day) return distribution as a cycle-agnostic cross-check.

Cycle anchors are the well-known BTC price lows/high (dates approximate, chosen from
the data, not tuned to results):
    2018-12-15  bear bottom   (~$3.2k)
    2021-11-10  cycle top     (~$69k)
    2022-11-21  bear bottom   (~$15.7k)
    2026-07-23  latest bar    (current cycle still running)

Usage:  python src/locked_cycles.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C
from strategies_v2 import trend_ensemble
from backtest import run_backtest
import metrics as M

DATA = Path(__file__).resolve().parent.parent / "data" / "BTCUSDT_1d_2017-08-01_2026-07-23.parquet"

CYCLES = [
    ("Cycle 1  trough->trough", "2018-12-15", "2022-11-21"),
    ("Cycle 2  trough->now (incomplete)", "2022-11-21", "2026-07-23"),
    ("Bull leg  bottom->top",  "2018-12-15", "2021-11-10"),
    ("Bear leg  top->bottom",  "2021-11-10", "2022-11-21"),
]


def load():
    df = pd.read_parquet(DATA)
    return df[~df.index.duplicated()].sort_index()


def seg(ret, bh, close, lo, hi):
    r = ret.loc[lo:hi]; b = bh.loc[lo:hi]; px = close.loc[lo:hi]
    days = (px.index[-1] - px.index[0]).days
    return {
        "days": days, "years": round(days / 365, 2),
        "strat_%": round(M.total_return(r) * 100, 1),
        "bh_%": round(M.total_return(b) * 100, 1),
        "strat_maxDD_%": round(M.max_drawdown(r) * 100, 1),
        "bh_maxDD_%": round(M.max_drawdown(b) * 100, 1),
        "px_start": round(float(px.iloc[0]), 0), "px_end": round(float(px.iloc[-1]), 0),
    }


def main():
    df = load()
    sig = trend_ensemble(df, C.THRESHOLD)
    ret = run_backtest(df, sig, C.FEE, C.SLIPPAGE, C.BARS_PER_YEAR).returns
    bh = df["close"].pct_change().fillna(0.0)
    close = df["close"]

    print("\n" + "=" * 100)
    print("ONE-CYCLE RETURNS — LOCKED spot strategy vs buy & hold (net of costs)")
    print("=" * 100)
    for name, lo, hi in CYCLES:
        s = seg(ret, bh, close, lo, hi)
        mult_s = 1 + s["strat_%"] / 100
        mult_b = 1 + s["bh_%"] / 100
        print(f"\n{name}   [{lo} -> {hi}]   ({s['years']} yr, BTC ${s['px_start']:,.0f} -> ${s['px_end']:,.0f})")
        print(f"    STRATEGY : {s['strat_%']:+.1f}%   (Rs10,000 -> Rs{10000*mult_s:,.0f}, {mult_s:.2f}x)   worst dip {s['strat_maxDD_%']}%")
        print(f"    BUY&HOLD : {s['bh_%']:+.1f}%   (Rs10,000 -> Rs{10000*mult_b:,.0f}, {mult_b:.2f}x)   worst dip {s['bh_maxDD_%']}%")

    # cycle-agnostic cross-check: rolling 4-year (1460d) return distribution
    print("\n" + "=" * 100)
    print("Cross-check: rolling 4-YEAR (1460-day) total return, all overlapping windows")
    print("=" * 100)
    for label, series in [("STRATEGY", ret), ("buy&hold", bh)]:
        roll = M.rolling_returns(series, 1460).dropna() * 100
        if len(roll):
            print(f"  {label:<10} min {roll.min():+.0f}%   median {roll.median():+.0f}%   "
                  f"max {roll.max():+.0f}%   (n={len(roll)} windows, {(roll>0).mean()*100:.0f}% positive)")


if __name__ == "__main__":
    main()
