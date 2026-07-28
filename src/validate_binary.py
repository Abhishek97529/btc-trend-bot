"""
Full robustness re-validation of the BINARY-full-exposure variant, run head-to-head
against the LOCKED fractional version on the identical battery used at lock time
(see validate.py): threshold sweep, ±30% window perturbation, cost stress, execution
lag, per-year returns, and a 2000-path block-bootstrap.

A real edge survives all of these. This lets the binary switch be judged at exactly
the same evidence bar as the original lock decision.

Usage:  python src/validate_binary.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indicators import ema, sma, donchian
from backtest import run_backtest
import metrics as M

BPY = 365
DATA = Path(__file__).resolve().parent.parent / "data" / "BTCUSDT_1d_2017-08-01_2026-07-23.parquet"
rng = np.random.default_rng(42)


def _votes(df, mult=1.0):
    c = df["close"]
    w = lambda n: max(2, int(round(n * mult)))
    v = pd.DataFrame(index=df.index)
    v["s50"] = (c > sma(c, w(50))).astype(float)
    v["s100"] = (c > sma(c, w(100))).astype(float)
    v["s200"] = (c > sma(c, w(200))).astype(float)
    v["e2050"] = (ema(c, w(20)) > ema(c, w(50))).astype(float)
    v["e50100"] = (ema(c, w(50)) > ema(c, w(100))).astype(float)
    up, dn = donchian(df, w(55))
    d = pd.Series(np.nan, index=df.index); d[c > up] = 1.0; d[c < dn] = 0.0
    v["don"] = d.ffill().fillna(0.0)
    v["mom"] = (c.pct_change(w(90)) > 0).astype(float)
    warm = c.rolling(w(200), min_periods=w(200)).mean().notna()
    return v.mean(axis=1), warm


def sig_frac(df, threshold=0.5, mult=1.0):
    f, warm = _votes(df, mult)
    return (f.where(f >= threshold, 0.0) * warm).fillna(0.0)


def sig_bin(df, threshold=0.5, mult=1.0):
    f, warm = _votes(df, mult)
    return ((f >= threshold).astype(float) * warm).fillna(0.0)


def stats_of(df, target, fee=0.001, slip=0.0005, lag_extra=0):
    t = target.shift(lag_extra) if lag_extra else target
    res = run_backtest(df, t, fee=fee, slippage=slip, bars_per_year=BPY)
    return M.summary(res.returns, BPY, res.trades, res.gross_exposure_time), res


def pair(tag, sf, sb):
    print(f"{tag:<26}  frac: ret={sf['total_return']*100:8.1f}% sharpe={sf['sharpe']:4.2f} "
          f"maxDD={sf['max_drawdown']*100:6.1f}% calmar={sf['calmar']:4.2f}   ||   "
          f"bin: ret={sb['total_return']*100:8.1f}% sharpe={sb['sharpe']:4.2f} "
          f"maxDD={sb['max_drawdown']*100:6.1f}% calmar={sb['calmar']:4.2f}")


def bootstrap(returns, label):
    r = returns.values; n = len(r); block = 30
    cagrs, sharpes, dds = [], [], []
    for _ in range(2000):
        idx = []
        while len(idx) < n:
            start = rng.integers(0, n - block)
            idx.extend(range(start, start + block))
        s = pd.Series(r[np.array(idx[:n])])
        cagrs.append(M.cagr(s, BPY)); sharpes.append(M.sharpe(s, BPY)); dds.append(M.max_drawdown(s))
    p = lambda a, q: np.percentile(a, q)
    print(f"  [{label}] CAGR 5/50/95: {p(cagrs,5)*100:5.0f}/{p(cagrs,50)*100:5.0f}/{p(cagrs,95)*100:5.0f}%"
          f"   Sharpe: {p(sharpes,5):.2f}/{p(sharpes,50):.2f}/{p(sharpes,95):.2f}"
          f"   maxDD: {p(dds,5)*100:.0f}/{p(dds,50)*100:.0f}/{p(dds,95)*100:.0f}%"
          f"   P(Sharpe>0.74)={np.mean(np.array(sharpes)>0.74)*100:.0f}%")


def main():
    df = pd.read_parquet(DATA)
    df = df[~df.index.duplicated()].sort_index().loc["2018-06-01":]
    print(f"\nSample: {df.index[0].date()} -> {df.index[-1].date()} ({len(df)} bars)")
    print("Legend: 'frac' = LOCKED fractional  ||  'bin' = binary full-exposure candidate\n")

    bf, resf = stats_of(df, sig_frac(df))
    bb, resb = stats_of(df, sig_bin(df))
    print("== BASELINE (threshold=0.5) ==========================================================")
    pair("baseline", bf, bb)

    print("\n== 1) THRESHOLD SENSITIVITY ==========================================================")
    for th in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]:
        sf, _ = stats_of(df, sig_frac(df, threshold=th))
        sb, _ = stats_of(df, sig_bin(df, threshold=th))
        pair(f"  threshold={th}", sf, sb)

    print("\n== 2) INTERNAL WINDOW PERTURBATION (±30%) ============================================")
    for m in [0.7, 0.85, 1.0, 1.15, 1.3]:
        sf, _ = stats_of(df, sig_frac(df, mult=m))
        sb, _ = stats_of(df, sig_bin(df, mult=m))
        pair(f"  window x{m}", sf, sb)

    print("\n== 3) TRANSACTION-COST SENSITIVITY ===================================================")
    for f, sl in [(0.001, 0.0005), (0.002, 0.001), (0.003, 0.0015), (0.005, 0.003)]:
        sf, _ = stats_of(df, sig_frac(df), fee=f, slip=sl)
        sb, _ = stats_of(df, sig_bin(df), fee=f, slip=sl)
        pair(f"  fee={f*100:.1f}% slip={sl*100:.2f}%", sf, sb)

    print("\n== 4) EXECUTION-LAG SENSITIVITY ======================================================")
    for lag in [0, 1, 2, 3]:
        sf, _ = stats_of(df, sig_frac(df), lag_extra=lag)
        sb, _ = stats_of(df, sig_bin(df), lag_extra=lag)
        pair(f"  +{lag} bar lag", sf, sb)

    print("\n== 5) PER-CALENDAR-YEAR RETURNS ======================================================")
    yr = pd.DataFrame({"frac": resf.returns, "bin": resb.returns,
                       "bh": df["close"].pct_change().fillna(0.0)})
    by = yr.groupby(yr.index.year).apply(lambda g: pd.Series({
        "frac_%": ((1 + g["frac"]).prod() - 1) * 100,
        "bin_%": ((1 + g["bin"]).prod() - 1) * 100,
        "bh_%": ((1 + g["bh"]).prod() - 1) * 100,
    }))
    print(by.round(1).to_string())

    print("\n== 6) BLOCK-BOOTSTRAP MONTE CARLO (30d blocks, 2000 paths) ===========================")
    bootstrap(resf.returns, "frac")
    bootstrap(resb.returns, "bin ")


if __name__ == "__main__":
    main()
