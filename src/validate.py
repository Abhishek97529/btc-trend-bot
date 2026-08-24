"""
Robustness battery for trend_ensemble. The goal is to try to BREAK it.
A real edge survives: parameter perturbation, higher costs, extra execution lag,
and bootstrap resampling. A curve-fit does not.

Everything here uses a FIXED threshold=0.5 (no optimization at all) unless a test
explicitly sweeps it -- so there is nothing tuned to the reported numbers.

Usage:  python src/validate.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_fetch import fetch_klines
from strategies_v2 import trend_ensemble, _trend_votes
from legacy_strategies import buy_and_hold
from backtest import run_backtest
import metrics as M
from indicators import ema, sma, donchian

BPY = 365
REPORTS = Path(__file__).resolve().parent.parent / "reports"
rng = np.random.default_rng(42)


def stats_of(df, target, fee=0.001, slip=0.0005, lag_extra=0):
    t = target.shift(lag_extra) if lag_extra else target
    res = run_backtest(df, t, fee=fee, slippage=slip, bars_per_year=BPY)
    s = M.summary(res.returns, BPY, res.trades, res.gross_exposure_time)
    return s, res


def line(tag, s):
    print(f"{tag:<34} ret={s['total_return']*100:8.1f}%  cagr={s['cagr']*100:6.1f}%  "
          f"sharpe={s['sharpe']:5.2f}  maxDD={s['max_drawdown']*100:6.1f}%  "
          f"calmar={s['calmar']:5.2f}  expo={s['exposure']*100:4.0f}%")


# Configurable-window version of the ensemble, to perturb the internal lengths.
def ensemble_custom(df, mult=1.0, threshold=0.5):
    c = df["close"]
    def w(n):
        return max(2, int(round(n * mult)))
    votes = pd.DataFrame(index=df.index)
    votes["s50"] = (c > sma(c, w(50))).astype(float)
    votes["s100"] = (c > sma(c, w(100))).astype(float)
    votes["s200"] = (c > sma(c, w(200))).astype(float)
    votes["e2050"] = (ema(c, w(20)) > ema(c, w(50))).astype(float)
    votes["e50100"] = (ema(c, w(50)) > ema(c, w(100))).astype(float)
    up, dn = donchian(df, w(55))
    d = pd.Series(np.nan, index=df.index); d[c > up] = 1.0; d[c < dn] = 0.0
    votes["don"] = d.ffill().fillna(0.0)
    votes["mom"] = (c.pct_change(w(90)) > 0).astype(float)
    frac = votes.mean(axis=1)
    pos = frac.where(frac >= threshold, 0.0)
    warm = c.rolling(w(200), min_periods=w(200)).mean().notna()
    return (pos * warm).fillna(0.0)


def main():
    df = fetch_klines("BTCUSDT", "1d", "2017-08-01")
    df = df[~df.index.duplicated()].sort_index()
    # Use the full post-warmup sample -- one honest picture across all regimes.
    df = df.loc["2018-06-01":]
    print(f"\nFull sample used: {df.index[0].date()} -> {df.index[-1].date()} ({len(df)} bars)\n")

    base_sig = trend_ensemble(df, threshold=0.5)
    bh_stats, _ = stats_of(df, buy_and_hold(df))
    base_stats, base_res = stats_of(df, base_sig)

    print("== BASELINE (fixed threshold=0.5, NO tuning) =========================================")
    line("buy_and_hold", bh_stats)
    line("trend_ensemble", base_stats)

    print("\n== 1) THRESHOLD SENSITIVITY (does it survive different votes?) =======================")
    for th in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]:
        s, _ = stats_of(df, trend_ensemble(df, threshold=th))
        line(f"  threshold={th}", s)

    print("\n== 2) INTERNAL WINDOW PERTURBATION (±30% on ALL lengths) ============================")
    for mult in [0.7, 0.85, 1.0, 1.15, 1.3]:
        s, _ = stats_of(df, ensemble_custom(df, mult=mult))
        line(f"  window x{mult}", s)

    print("\n== 3) TRANSACTION-COST SENSITIVITY ==================================================")
    for f, sl in [(0.001, 0.0005), (0.002, 0.001), (0.003, 0.0015), (0.005, 0.003)]:
        s, _ = stats_of(df, base_sig, fee=f, slip=sl)
        line(f"  fee={f*100:.1f}% slip={sl*100:.2f}%", s)

    print("\n== 4) EXECUTION-LAG SENSITIVITY (trade later than assumed) ==========================")
    for lag in [0, 1, 2, 3]:
        s, _ = stats_of(df, base_sig, lag_extra=lag)
        line(f"  +{lag} bar lag", s)

    print("\n== 5) PER-CALENDAR-YEAR RETURNS (trend_ensemble vs buy&hold) ========================")
    yr = pd.DataFrame({"strat": base_res.returns,
                       "bh": df["close"].pct_change().fillna(0.0)})
    by = yr.groupby(yr.index.year).apply(lambda g: pd.Series({
        "strat_%": ((1 + g["strat"]).prod() - 1) * 100,
        "bh_%": ((1 + g["bh"]).prod() - 1) * 100,
    }))
    print(by.round(1).to_string())

    print("\n== 6) BLOCK-BOOTSTRAP MONTE CARLO (30-day blocks, 2000 paths) =======================")
    r = base_res.returns.values
    n = len(r)
    block = 30
    cagrs, sharpes, dds = [], [], []
    for _ in range(2000):
        idx = []
        while len(idx) < n:
            start = rng.integers(0, n - block)
            idx.extend(range(start, start + block))
        sample = pd.Series(r[np.array(idx[:n])])
        cagrs.append(M.cagr(sample, BPY))
        sharpes.append(M.sharpe(sample, BPY))
        dds.append(M.max_drawdown(sample))
    def pct(a, p): return np.percentile(a, p)
    print(f"  CAGR    5th/50th/95th : {pct(cagrs,5)*100:6.1f}% / {pct(cagrs,50)*100:6.1f}% / {pct(cagrs,95)*100:6.1f}%")
    print(f"  Sharpe  5th/50th/95th : {pct(sharpes,5):6.2f}  / {pct(sharpes,50):6.2f}  / {pct(sharpes,95):6.2f}")
    print(f"  MaxDD   5th/50th/95th : {pct(dds,5)*100:6.1f}% / {pct(dds,50)*100:6.1f}% / {pct(dds,95)*100:6.1f}%")
    print(f"  P(Sharpe > 0.74 = buy&hold's): {np.mean(np.array(sharpes) > 0.74)*100:.0f}%")


if __name__ == "__main__":
    main()
