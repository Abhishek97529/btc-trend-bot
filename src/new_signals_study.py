"""
Push the frontier OUTWARD: test genuinely NEW signal inputs — ones orthogonal to
the 7 price-trend votes the locked ensemble already uses. Everything else has only
resized/re-leveraged the SAME signals; this tests whether new information helps.

New inputs (none used by the locked 7 votes):
  A. VOLUME / accumulation  — OBV trend (price ignores volume entirely today)
  B. CROSS-ASSET BREADTH    — fraction of BTC/ETH/BNB/ADA/XRP/SOL in uptrend
  C. VOLATILITY REGIME      — calm vs. stressed realized-vol regime

Each tested two honest ways, keeping the locked machinery intact:
  * as an 8th VOTE  (mean of 8 votes, same 0.50 gate)
  * as a FILTER     (locked 7-vote target, zeroed/halved when the new input is risk-off)

Discipline: causal signals, full risk metrics, split into design-era (pre-2023,
in-sample) vs. UNSEEN (2023+). A real edge must improve the OUT-OF-SAMPLE
risk-adjusted numbers — not just total return, and not just in-sample.

Usage:  python src/new_signals_study.py
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
DATA = Path(__file__).resolve().parent.parent / "data"
BTC = DATA / "BTCUSDT_1d_2017-08-01_2026-07-23.parquet"
START, SPLIT = "2018-06-01", "2023-01-01"
ASSETS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT", "XRPUSDT", "SOLUSDT"]


def load(sym):
    df = pd.read_parquet(DATA / f"{sym}_1d_2017-08-01_2026-07-23.parquet")
    return df[~df.index.duplicated()].sort_index()


# ---- the locked 7 votes ------------------------------------------------------ #
def base_votes(df):
    c = df["close"]
    v = pd.DataFrame(index=df.index)
    v["sma50"] = (c > sma(c, 50)).astype(float)
    v["sma100"] = (c > sma(c, 100)).astype(float)
    v["sma200"] = (c > sma(c, 200)).astype(float)
    v["ema20_50"] = (ema(c, 20) > ema(c, 50)).astype(float)
    v["ema50_100"] = (ema(c, 50) > ema(c, 100)).astype(float)
    up, dn = donchian(df, 55)
    d = pd.Series(np.nan, index=df.index); d[c > up] = 1.0; d[c < dn] = 0.0
    v["don55"] = d.ffill().fillna(0.0)
    v["mom90"] = (c.pct_change(90) > 0).astype(float)
    return v


def warmup(df):
    return df["close"].rolling(200, min_periods=200).mean().notna()


# ---- NEW candidate inputs (all causal) --------------------------------------- #
def vote_volume(df):
    """A: OBV (on-balance volume) rising vs its own 30d average = accumulation."""
    obv = (np.sign(df["close"].diff()).fillna(0.0) * df["volume"]).cumsum()
    return (obv > sma(obv, 30)).astype(float)


def vote_breadth(btc_index):
    """B: fraction of the 6 majors above their own SMA(100) >= 0.5 (broad uptrend)."""
    ups = []
    for sym in ASSETS:
        d = load(sym)
        u = (d["close"] > sma(d["close"], 100)).astype(float).reindex(btc_index)
        ups.append(u)
    breadth = pd.concat(ups, axis=1).mean(axis=1, skipna=True)  # skips assets w/o history
    return (breadth >= 0.5).astype(float), breadth


def vote_volregime(df):
    """C: realized 30d vol below its trailing 180d median = calm regime (risk-on)."""
    vol = rolling_vol(df["close"].pct_change(), 30) * np.sqrt(BPY)
    med = vol.rolling(180, min_periods=90).median()
    return (vol <= med).astype(float)


# ---- assembly ---------------------------------------------------------------- #
def locked_target(df, thr=0.5):
    f = base_votes(df).mean(axis=1)
    return (f.where(f >= thr, 0.0) * warmup(df)).fillna(0.0)


def eighth_vote(df, new_vote, thr=0.5):
    v = base_votes(df).copy()
    v["new"] = new_vote.reindex(df.index).fillna(0.0)
    f = v.mean(axis=1)
    return (f.where(f >= thr, 0.0) * warmup(df)).fillna(0.0)


def filtered(df, new_vote, hard=True):
    base = locked_target(df)
    nv = new_vote.reindex(df.index).fillna(0.0)
    mult = nv if hard else (0.5 + 0.5 * nv)
    return (base * mult).fillna(0.0)


def stats(df, target, label):
    r = run_backtest(df, target, fee=0.001, slippage=0.0005, bars_per_year=BPY)
    ret = r.returns
    return {"strategy": label, "ret%": M.total_return(ret) * 100, "cagr%": M.cagr(ret, BPY) * 100,
            "sharpe": M.sharpe(ret, BPY), "sortino": M.sortino(ret, BPY),
            "maxDD%": M.max_drawdown(ret) * 100, "calmar": M.calmar(ret, BPY),
            "expo%": r.gross_exposure_time * 100, "trades": r.trades}


def window(df, cands, lo, hi, tag):
    sl = df.loc[lo:hi] if hi else df.loc[lo:]
    idx = sl.index
    rows = [stats(sl, sig.reindex(idx), name) for name, sig in cands]
    out = pd.DataFrame(rows).set_index("strategy").round(2)
    print(f"\n{'='*104}\n{tag}  ({idx[0].date()} -> {idx[-1].date()}, {len(idx)} bars)\n{'='*104}")
    print(out.to_string())


def main():
    df = load("BTCUSDT").loc[START:]
    vb, breadth_raw = vote_breadth(df.index)
    cands = [
        ("locked (7-vote)",        locked_target(df)),
        ("A volume  +8th vote",    eighth_vote(df, vote_volume(df))),
        ("A volume  filter",       filtered(df, vote_volume(df), hard=True)),
        ("B breadth +8th vote",    eighth_vote(df, vb)),
        ("B breadth filter",       filtered(df, vb, hard=True)),
        ("B breadth soft-filter",  filtered(df, vb, hard=False)),
        ("C volregime +8th vote",  eighth_vote(df, vote_volregime(df))),
        ("C volregime filter",     filtered(df, vote_volregime(df), hard=True)),
        ("buy_and_hold",           pd.Series(1.0, index=df.index)),
    ]
    window(df, cands, START, None, "FULL PERIOD")
    window(df, cands, START, SPLIT, "DESIGN ERA (pre-2023, in-sample)")
    window(df, cands, SPLIT, None, "UNSEEN (2023+, out-of-sample)")


if __name__ == "__main__":
    main()
