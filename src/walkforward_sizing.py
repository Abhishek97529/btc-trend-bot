"""
Walk-forward: fractional (LOCKED) vs. binary-full exposure.

Both rules are PARAMETER-FREE (gate fixed at 0.50), so there is nothing to re-fit
per window. The honest walk-forward here is a stability/consistency test:

  * Step through many consecutive, NON-overlapping test windows.
  * Signals are computed on full history (warmup) but each window is scored only on
    its own bars — the equity inside a window starts fresh at 1.0.
  * Report per-window win/loss, aggregate stitched out-of-sample metrics, and a
    year-by-year breakdown. If binary's return edge is real (not a 2020-21 artifact)
    it should win a majority of independent windows WITHOUT worse drawdowns.

Usage:  python src/walkforward_sizing.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indicators import sma, ema, donchian
from backtest import run_backtest
import metrics as M

BPY = 365
DATA = Path(__file__).resolve().parent.parent / "data" / "BTCUSDT_1d_2017-08-01_2026-07-23.parquet"
START = "2018-06-01"


def votes(df):
    c = df["close"]
    v = pd.DataFrame(index=df.index)
    v["sma50"] = (c > sma(c, 50)).astype(float)
    v["sma100"] = (c > sma(c, 100)).astype(float)
    v["sma200"] = (c > sma(c, 200)).astype(float)
    v["ema20_50"] = (ema(c, 20) > ema(c, 50)).astype(float)
    v["ema50_100"] = (ema(c, 50) > ema(c, 100)).astype(float)
    up, dn = donchian(df, 55)
    d = pd.Series(np.nan, index=df.index)
    d[c > up] = 1.0
    d[c < dn] = 0.0
    v["don55"] = d.ffill().fillna(0.0)
    v["mom90"] = (c.pct_change(90) > 0).astype(float)
    return v


def warmup(df):
    return df["close"].rolling(200, min_periods=200).mean().notna()


def fractional(df, thr=0.5):
    f = votes(df).mean(axis=1)
    return (f.where(f >= thr, 0.0) * warmup(df)).fillna(0.0)


def binary(df, thr=0.5):
    f = votes(df).mean(axis=1)
    return ((f >= thr).astype(float) * warmup(df)).fillna(0.0)


def win_metrics(df_win, sig_win):
    res = run_backtest(df_win, sig_win, fee=0.001, slippage=0.0005, bars_per_year=BPY)
    r = res.returns
    return {
        "ret": M.total_return(r),
        "sharpe": M.sharpe(r, BPY),
        "maxDD": M.max_drawdown(r),
    }


def walk(df, sig_frac, sig_bin, window_days, label):
    """Non-overlapping windows of window_days; score each independently."""
    idx = df.index
    rows = []
    frac_oos, bin_oos = [], []          # stitched OOS per-bar returns
    start = 0
    while start + window_days <= len(idx):
        sl = df.iloc[start:start + window_days]
        wi = sl.index
        f = win_metrics(sl, sig_frac.reindex(wi))
        b = win_metrics(sl, sig_bin.reindex(wi))
        # stitched OOS returns
        frac_oos.append(run_backtest(sl, sig_frac.reindex(wi), 0.001, 0.0005, BPY).returns)
        bin_oos.append(run_backtest(sl, sig_bin.reindex(wi), 0.001, 0.0005, BPY).returns)
        rows.append({
            "window": f"{wi[0].date()}..{wi[-1].date()}",
            "frac_ret%": f["ret"] * 100, "bin_ret%": b["ret"] * 100,
            "bin_wins": b["ret"] > f["ret"],
            "frac_DD%": f["maxDD"] * 100, "bin_DD%": b["maxDD"] * 100,
            "bin_worse_DD": b["maxDD"] < f["maxDD"] - 1e-6,
        })
        start += window_days
    wf = pd.DataFrame(rows)
    fr = pd.concat(frac_oos); bn = pd.concat(bin_oos)
    print(f"\n{'='*92}\nWALK-FORWARD  window={window_days}d  ({label})  |  {len(wf)} independent windows\n{'='*92}")
    print(wf.round(1).to_string(index=False))
    n = len(wf)
    print(f"\n  binary beat fractional on RETURN in {wf['bin_wins'].sum()}/{n} windows "
          f"({wf['bin_wins'].mean()*100:.0f}%)")
    print(f"  binary had WORSE drawdown in       {wf['bin_worse_DD'].sum()}/{n} windows "
          f"({wf['bin_worse_DD'].mean()*100:.0f}%)")
    print(f"  stitched OOS Sharpe   frac={M.sharpe(fr,BPY):.2f}   bin={M.sharpe(bn,BPY):.2f}")
    print(f"  stitched OOS return   frac={M.total_return(fr)*100:.0f}%   bin={M.total_return(bn)*100:.0f}%")
    print(f"  stitched OOS maxDD    frac={M.max_drawdown(fr)*100:.1f}%   bin={M.max_drawdown(bn)*100:.1f}%")
    return wf


def yearly(df, sig_frac, sig_bin):
    print(f"\n{'='*92}\nYEAR-BY-YEAR (independent, each year scored fresh)\n{'='*92}")
    rows = []
    for yr, sl in df.groupby(df.index.year):
        if len(sl) < 60:
            continue
        wi = sl.index
        f = win_metrics(sl, sig_frac.reindex(wi))
        b = win_metrics(sl, sig_bin.reindex(wi))
        bh = M.total_return(sl["close"].pct_change().fillna(0.0)) * 100
        rows.append({
            "year": yr, "B&H%": bh,
            "frac%": f["ret"] * 100, "bin%": b["ret"] * 100,
            "edge_pp": (b["ret"] - f["ret"]) * 100,
            "frac_DD%": f["maxDD"] * 100, "bin_DD%": b["maxDD"] * 100,
        })
    print(pd.DataFrame(rows).round(1).to_string(index=False))


def main():
    df = pd.read_parquet(DATA)
    df = df[~df.index.duplicated()].sort_index().loc[START:]
    sig_frac = fractional(df)
    sig_bin = binary(df)
    yearly(df, sig_frac, sig_bin)
    walk(df, sig_frac, sig_bin, 180, "6-month")
    walk(df, sig_frac, sig_bin, 365, "annual")


if __name__ == "__main__":
    main()
