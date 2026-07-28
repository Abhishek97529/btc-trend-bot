"""
ROLLING RETURNS for the LOCKED SPOT strategy (trend_ensemble, threshold 0.5) vs
buy & hold. Rolling total return over trailing windows of 30 / 90 / 180 / 365 days.

For each horizon we report the distribution across all overlapping windows
(min / 5th / median / mean / 95th / max) and the % of windows that were positive
("hold-for-N-days win rate"), plus the exact best & worst window dates.

Usage:  python src/locked_rolling.py
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
WINDOWS = [30, 90, 180, 365]


def load():
    df = pd.read_parquet(DATA)
    return df[~df.index.duplicated()].sort_index().loc[C.BACKTEST_START:]


def dist_row(roll: pd.Series, label: str, win: int) -> dict:
    r = roll.dropna() * 100
    return {
        "series": label,
        "window_d": win,
        "n_win": len(r),
        "min_%": round(r.min(), 1),
        "p5_%": round(np.percentile(r, 5), 1),
        "median_%": round(r.median(), 1),
        "mean_%": round(r.mean(), 1),
        "p95_%": round(np.percentile(r, 95), 1),
        "max_%": round(r.max(), 1),
        "pos_%": round((r > 0).mean() * 100, 0),
    }


def extremes(roll: pd.Series, win: int, label: str):
    r = roll.dropna()
    bi, wi = r.idxmax(), r.idxmin()
    return (f"  [{label} {win}d]  best {r.max()*100:+.1f}% "
            f"(window ending {bi.date()}, i.e. {(bi - pd.Timedelta(days=win-1)).date()}->{bi.date()})"
            f"   |  worst {r.min()*100:+.1f}% "
            f"(ending {wi.date()})")


def main():
    df = load()
    sig = trend_ensemble(df, C.THRESHOLD)
    ret = run_backtest(df, sig, C.FEE, C.SLIPPAGE, C.BARS_PER_YEAR).returns
    bh = df["close"].pct_change().fillna(0.0)

    print("\n" + "=" * 96)
    print("ROLLING TOTAL RETURNS  —  LOCKED SPOT strategy vs BUY & HOLD")
    print(f"  Sample {df.index[0].date()} -> {df.index[-1].date()}  ({len(df)} daily bars, overlapping windows)")
    print("=" * 96)

    rows = []
    for w in WINDOWS:
        rows.append(dist_row(M.rolling_returns(ret, w), "LOCKED", w))
        rows.append(dist_row(M.rolling_returns(bh, w), "buy&hold", w))
    tbl = pd.DataFrame(rows)
    print(tbl.to_string(index=False))

    print("\n" + "-" * 96)
    print("BEST / WORST windows (LOCKED)")
    print("-" * 96)
    for w in WINDOWS:
        print(extremes(M.rolling_returns(ret, w), w, "LOCKED"))

    print("\n" + "-" * 96)
    print("READ: 'pos_%' = share of overlapping N-day windows that finished positive")
    print("      (a hold-for-N-days historical win rate).  1-yr = 365d.")
    print("-" * 96)


if __name__ == "__main__":
    main()
