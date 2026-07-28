"""
Profitability study — honest exploration of whether the LOCKED trend_ensemble can
be improved. NOT for tuning into config. Every candidate is reported with FULL risk
metrics (not just return) and split into pre-2023 (design era) / 2023+ (unseen) so
we can see what is a real structural edge vs. what only looks good in-sample.

Baseline = the locked strategy (fractional exposure = agreement fraction, gate 0.50).

Usage:  python src/optimize_study.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indicators import sma, ema, donchian, rolling_vol
from backtest import run_backtest
import metrics as M

BPY = 365
DATA = Path(__file__).resolve().parent.parent / "data" / "BTCUSDT_1d_2017-08-01_2026-07-23.parquet"
START = "2018-06-01"
SPLIT = "2023-01-01"


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


# ---- candidate exposure rules ------------------------------------------------ #
def baseline(df, thr=0.5):
    """LOCKED: exposure = agreement fraction, gated at thr."""
    f = votes(df).mean(axis=1)
    return (f.where(f >= thr, 0.0) * warmup(df)).fillna(0.0)


def binary(df, thr=0.5):
    """Full 100% when gate passes, else flat. Captures more bull upside."""
    f = votes(df).mean(axis=1)
    return ((f >= thr).astype(float) * warmup(df)).fillna(0.0)


def rescaled(df, thr=0.5):
    """Map the gated range [thr,1] -> [0,1] so strong agreement => full size."""
    f = votes(df).mean(axis=1)
    scaled = ((f - thr) / (1 - thr)).clip(0, 1)
    return (scaled.where(f >= thr, 0.0) * warmup(df)).fillna(0.0)


def convex(df, thr=0.5):
    """Fractional but squared -> lean in harder as more signals agree."""
    f = votes(df).mean(axis=1)
    return ((f ** 2).where(f >= thr, 0.0) * warmup(df)).fillna(0.0)


def vol_target(df, tv=0.60, win=30):
    """Binary gate, sized to a target annual vol (cap 1.0, no leverage)."""
    f = votes(df).mean(axis=1)
    direction = (f >= 0.5).astype(float)
    realized = rolling_vol(df["close"].pct_change(), win) * np.sqrt(BPY)
    scale = (tv / realized).clip(0, 1)
    return (direction * scale * warmup(df)).fillna(0.0)


def bh(df):
    return pd.Series(1.0, index=df.index)


# ---- evaluation -------------------------------------------------------------- #
def stats(df, target, label):
    res = run_backtest(df, target, fee=0.001, slippage=0.0005, bars_per_year=BPY)
    r = res.returns
    return {
        "strategy": label,
        "ret%": M.total_return(r) * 100,
        "cagr%": M.cagr(r, BPY) * 100,
        "vol%": M.ann_vol(r, BPY) * 100,
        "sharpe": M.sharpe(r, BPY),
        "sortino": M.sortino(r, BPY),
        "maxDD%": M.max_drawdown(r) * 100,
        "calmar": M.calmar(r, BPY),
        "expo%": res.gross_exposure_time * 100,
        "trades": res.trades,
    }


def run_window(df_full, lo, hi, tag):
    # compute signal on full history (warmup), evaluate on the window
    sl = df_full.loc[lo:hi] if hi else df_full.loc[lo:]
    idx = sl.index
    cands = [
        ("baseline (LOCKED)", baseline(df_full)),
        ("binary_full", binary(df_full)),
        ("binary thr=0.43", binary(df_full, 3/7 - 1e-9)),
        ("binary thr=0.57", binary(df_full, 4/7 - 1e-9)),
        ("rescaled", rescaled(df_full)),
        ("convex(f^2)", convex(df_full)),
        ("vol_target 0.6", vol_target(df_full, 0.6, 30)),
        ("vol_target 0.8", vol_target(df_full, 0.8, 30)),
        ("buy_and_hold", bh(df_full)),
    ]
    rows = []
    for name, sig in cands:
        rows.append(stats(sl, sig.reindex(idx), name))
    out = pd.DataFrame(rows).set_index("strategy")
    print(f"\n{'='*100}\n{tag}  ({idx[0].date()} -> {idx[-1].date()}, {len(idx)} bars)\n{'='*100}")
    print(out.round(2).to_string())
    return out


def main():
    df = pd.read_parquet(DATA)
    df = df[~df.index.duplicated()].sort_index().loc[START:]
    run_window(df, START, None, "FULL PERIOD")
    run_window(df, START, SPLIT, "DESIGN ERA  (pre-2023, in-sample)")
    run_window(df, SPLIT, None, "UNSEEN      (2023+, out-of-sample)")


if __name__ == "__main__":
    main()
