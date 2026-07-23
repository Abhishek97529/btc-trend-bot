"""
Does adding a trailing stop-loss improve trend_ensemble? Measure, don't guess.

A trailing stop tracks the highest close since entry and forces the position flat
if price falls more than `stop` from that peak (staying flat until the strategy
itself resets to cash and re-enters). We test fixed-% stops and an ATR-based
"chandelier" stop, all on the full sample with the fixed threshold=0.5 baseline.

Usage:  python src/test_trailing_stop.py
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
from indicators import atr
import metrics as M

BPY = 365
FEE, SLIP = 0.001, 0.0005


def apply_trailing_stop(df, target, stop_pct=None, atr_mult=None, atr_n=22):
    """Return a stop-adjusted target series. Peak tracked per in-market episode."""
    close = df["close"].values
    tgt = target.reindex(df.index).fillna(0.0).values
    a = (atr(df, atr_n) / df["close"]).fillna(0.0).values if atr_mult else None
    out = np.zeros(len(tgt))
    stopped = False
    peak = -np.inf
    for i in range(len(tgt)):
        if tgt[i] <= 0:
            stopped, peak = False, -np.inf
            continue
        if stopped:
            continue  # forced flat until strategy resets
        peak = max(peak, close[i])
        thresh = stop_pct if stop_pct is not None else atr_mult * a[i]
        if close[i] <= peak * (1 - thresh):
            stopped = True  # stop hit -> flat this bar and onward
        else:
            out[i] = tgt[i]
    return pd.Series(out, index=df.index)


def evalu(df, target, tag):
    res = run_backtest(df, target, fee=FEE, slippage=SLIP, bars_per_year=BPY)
    s = M.summary(res.returns, BPY, res.trades, res.gross_exposure_time)
    print(f"{tag:<24} ret={s['total_return']*100:8.1f}%  cagr={s['cagr']*100:6.1f}%  "
          f"sharpe={s['sharpe']:5.2f}  sortino={s['sortino']:5.2f}  "
          f"maxDD={s['max_drawdown']*100:6.1f}%  calmar={s['calmar']:5.2f}  "
          f"expo={s['exposure']*100:4.0f}%")
    return s


def main():
    df = fetch_klines("BTCUSDT", "1d", "2017-08-01")
    df = df[~df.index.duplicated()].sort_index().loc["2018-06-01":]
    base = trend_ensemble(df, threshold=0.5)

    print(f"\nSample {df.index[0].date()} -> {df.index[-1].date()}\n")
    print("== BASELINE (no stop) ==============================================================")
    b = evalu(df, base, "no_stop")

    print("\n== FIXED-% TRAILING STOP ===========================================================")
    for pct in [0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30]:
        evalu(df, apply_trailing_stop(df, base, stop_pct=pct), f"stop {int(pct*100)}%")

    print("\n== ATR CHANDELIER TRAILING STOP ====================================================")
    for m in [2, 3, 4, 5, 6]:
        evalu(df, apply_trailing_stop(df, base, atr_mult=m), f"stop {m}xATR(22)")

    print("\nBaseline for reference:")
    print(f"  no_stop -> sharpe {b['sharpe']:.2f}, return {b['total_return']*100:.0f}%, "
          f"maxDD {b['max_drawdown']*100:.1f}%")


if __name__ == "__main__":
    main()
