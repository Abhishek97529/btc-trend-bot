"""
Robustness battery for the LEVERAGED futures variant: LF constant 2x.

Same battery as src/validate.py (the goal is to BREAK it), but every run goes
through the perpetual-futures model instead of the spot engine:
  - constant 2x whenever the trend gate is long, flat otherwise
  - funding (~11.7%/yr on notional) + fees/slippage on leveraged turnover
  - intraday liquidation on the low (a 2x long needs a ~50% intraday drop to die)

Nothing is tuned to the numbers: threshold is fixed at 0.5 unless a test sweeps it.

Usage:  python src/validate_futures_2x.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_fetch import fetch_klines
from strategies_v2 import trend_ensemble
from indicators import ema, sma, donchian
import metrics as M

BPY = 365
CAP = 2.0
FUNDING_DAILY = 0.117 / 365
MAINT = 0.005
rng = np.random.default_rng(42)


def lev_from_signal(sig, lag_extra=0):
    """Constant-CAP long/flat leverage from a [0,1] signal, executed on t+1 (+lag)."""
    frac = sig.shift(1 + lag_extra).fillna(0.0)
    return (frac > 0).astype(float) * CAP


def run_leveraged(df, lev, underlying, low_ret, fee=0.001, slip=0.0005):
    """Daily net returns for the constant-leverage long/flat perp. Returns (stats, liq)."""
    rets = np.zeros(len(lev))
    prev_notional = 0.0
    liq_idx = None
    for t in range(len(lev)):
        notional = float(lev.iloc[t])
        if notional > 0 and notional * low_ret.iloc[t] <= -(1 - MAINT):
            rets[t] = -1.0
            liq_idx = t
            break
        rets[t] = (notional * underlying.iloc[t]
                   - FUNDING_DAILY * notional
                   - abs(notional - prev_notional) * (fee + slip))
        prev_notional = notional
    r = pd.Series(rets, index=df.index)
    trades = int(((lev > 0).astype(int).diff().fillna(lev.iloc[0] > 0) == 1).sum())
    s = M.summary(r, BPY, trades, float((lev > 0).mean()))
    return s, r, liq_idx


def line(tag, s, liq=None):
    tail = "  LIQUIDATED" if liq is not None else ""
    print(f"{tag:<34} ret={s['total_return']*100:9.1f}%  cagr={s['cagr']*100:6.1f}%  "
          f"sharpe={s['sharpe']:5.2f}  maxDD={s['max_drawdown']*100:6.1f}%  "
          f"calmar={s['calmar']:5.2f}  expo={s['exposure']*100:4.0f}%{tail}")


# Configurable-window ensemble to perturb the internal lengths (mirrors validate.py).
def ensemble_custom(df, mult=1.0, threshold=0.5):
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
    frac = v.mean(axis=1)
    pos = frac.where(frac >= threshold, 0.0)
    warm = c.rolling(w(200), min_periods=w(200)).mean().notna()
    return (pos * warm).fillna(0.0)


def main():
    df = fetch_klines("BTCUSDT", "1d", "2017-08-01")
    df = df[~df.index.duplicated()].sort_index().loc["2018-06-01":]
    underlying = df["close"].pct_change().fillna(0.0)
    low_ret = (df["low"] / df["close"].shift(1) - 1).fillna(0.0)
    print(f"\nLEVERAGED battery | LF constant {CAP:g}x | {df.index[0].date()} -> "
          f"{df.index[-1].date()} ({len(df)} bars)")
    print("Model: funding ~11.7%/yr + intraday liquidation. Threshold fixed 0.5 unless swept.\n")

    def run(sig, **kw):
        return run_leveraged(df, lev_from_signal(sig, kw.pop("lag_extra", 0)),
                             underlying, low_ret, **kw)

    base_sig = trend_ensemble(df, threshold=0.5)
    base_s, base_r, base_liq = run(base_sig)

    print("== BASELINE (fixed threshold=0.5, NO tuning) =========================================")
    line(f"buy_and_hold 1x", M.summary(underlying, BPY, 1, 1.0))
    line(f"LF constant {CAP:g}x", base_s, base_liq)

    print("\n== 1) THRESHOLD SENSITIVITY =========================================================")
    for th in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]:
        s, _, liq = run(trend_ensemble(df, threshold=th))
        line(f"  threshold={th}", s, liq)

    print("\n== 2) INTERNAL WINDOW PERTURBATION (+/-30% on ALL lengths) ==========================")
    for mult in [0.7, 0.85, 1.0, 1.15, 1.3]:
        s, _, liq = run(ensemble_custom(df, mult=mult))
        line(f"  window x{mult}", s, liq)

    print("\n== 3) TRANSACTION-COST SENSITIVITY (fees on leveraged turnover) =====================")
    for f, sl in [(0.001, 0.0005), (0.002, 0.001), (0.003, 0.0015), (0.005, 0.003)]:
        s, _, liq = run(base_sig, fee=f, slip=sl)
        line(f"  fee={f*100:.1f}% slip={sl*100:.2f}%", s, liq)

    print("\n== 4) EXECUTION-LAG SENSITIVITY =====================================================")
    for lag in [0, 1, 2, 3]:
        s, _, liq = run(base_sig, lag_extra=lag)
        line(f"  +{lag} bar lag", s, liq)

    print("\n== 5) PER-CALENDAR-YEAR RETURNS (2x vs buy&hold) ====================================")
    yr = pd.DataFrame({"strat": base_r, "bh": underlying})
    by = yr.groupby(yr.index.year).apply(lambda g: pd.Series({
        "strat_%": ((1 + g["strat"]).prod() - 1) * 100,
        "bh_%": ((1 + g["bh"]).prod() - 1) * 100,
    }))
    print(by.round(1).to_string())

    print("\n== 6) BLOCK-BOOTSTRAP MONTE CARLO (30-day blocks, 2000 paths) =======================")
    r = base_r.values
    n, block = len(r), 30
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
    pct = lambda a, p: np.percentile(a, p)
    print(f"  CAGR    5th/50th/95th : {pct(cagrs,5)*100:6.1f}% / {pct(cagrs,50)*100:6.1f}% / {pct(cagrs,95)*100:6.1f}%")
    print(f"  Sharpe  5th/50th/95th : {pct(sharpes,5):6.2f}  / {pct(sharpes,50):6.2f}  / {pct(sharpes,95):6.2f}")
    print(f"  MaxDD   5th/50th/95th : {pct(dds,5)*100:6.1f}% / {pct(dds,50)*100:6.1f}% / {pct(dds,95)*100:6.1f}%")
    print(f"  P(Sharpe > 0.74 = buy&hold's) : {np.mean(np.array(sharpes) > 0.74)*100:.0f}%")
    print(f"  P(Sharpe > 1.18 = spot 1x's)  : {np.mean(np.array(sharpes) > 1.18)*100:.0f}%")


if __name__ == "__main__":
    main()
