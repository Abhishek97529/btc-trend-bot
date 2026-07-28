"""
Consistency check for the two survivors of new_signals_study.py:
  * volume (OBV) as an 8th vote
  * vol-regime as an 8th vote
vs. the locked 7-vote baseline.

A single good out-of-sample window (2023+) is not evidence. Here we score many
independent, non-overlapping windows and a bootstrap, and ask: does the candidate
beat locked CONSISTENTLY on Sharpe / drawdown, or was 2023+ a one-window fluke?

Usage:  python src/new_signals_walkforward.py
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
START = "2018-06-01"
rng = np.random.default_rng(42)


def load(sym):
    df = pd.read_parquet(DATA / f"{sym}_1d_2017-08-01_2026-07-23.parquet")
    return df[~df.index.duplicated()].sort_index()


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


def vote_volume(df):
    obv = (np.sign(df["close"].diff()).fillna(0.0) * df["volume"]).cumsum()
    return (obv > sma(obv, 30)).astype(float)


def vote_volregime(df):
    vol = rolling_vol(df["close"].pct_change(), 30) * np.sqrt(BPY)
    med = vol.rolling(180, min_periods=90).median()
    return (vol <= med).astype(float)


def locked(df):
    f = base_votes(df).mean(axis=1)
    return (f.where(f >= 0.5, 0.0) * warmup(df)).fillna(0.0)


def eighth(df, nv):
    v = base_votes(df).copy(); v["new"] = nv.reindex(df.index).fillna(0.0)
    f = v.mean(axis=1)
    return (f.where(f >= 0.5, 0.0) * warmup(df)).fillna(0.0)


def m(df_win, sig):
    r = run_backtest(df_win, sig, 0.001, 0.0005, BPY).returns
    return M.sharpe(r, BPY), M.total_return(r), M.max_drawdown(r), r


def walk(df, sigs, wd, tag):
    """sigs: dict name->full-history Series. Non-overlapping windows of wd days."""
    idx = df.index
    names = list(sigs)
    rows, stitched = [], {n: [] for n in names}
    start = 0
    while start + wd <= len(idx):
        wi = df.iloc[start:start + wd].index
        sh = {}
        for n in names:
            s, ret, dd, r = m(df.loc[wi[0]:wi[-1]], sigs[n].reindex(wi))
            sh[n] = s; stitched[n].append(r)
        rows.append({"window": f"{wi[0].date()}", **{f"{n}_Sh": sh[n] for n in names}})
        start += wd
    wf = pd.DataFrame(rows)
    print(f"\n{'='*88}\nWALK-FORWARD  window={wd}d ({tag})  |  {len(wf)} windows — Sharpe per window\n{'='*88}")
    print(wf.round(2).to_string(index=False))
    base = names[0]
    for n in names[1:]:
        wins = (wf[f"{n}_Sh"] > wf[f"{base}_Sh"] + 1e-9).sum()
        print(f"  {n:<22} beat locked on Sharpe in {wins}/{len(wf)} windows ({wins/len(wf)*100:.0f}%)")
    for n in names:
        st = pd.concat(stitched[n])
        print(f"  stitched {n:<22} Sharpe={M.sharpe(st,BPY):.3f}  maxDD={M.max_drawdown(st)*100:6.1f}%  ret={M.total_return(st)*100:7.0f}%")


def bootstrap(df, sigs):
    print(f"\n{'='*88}\nBLOCK-BOOTSTRAP (30d blocks, 2000 paths) — median Sharpe / median maxDD\n{'='*88}")
    rets = {n: run_backtest(df, s, 0.001, 0.0005, BPY).returns.values for n, s in sigs.items()}
    n = len(next(iter(rets.values()))); block = 30
    res = {k: {"sh": [], "dd": []} for k in sigs}
    for _ in range(2000):
        ix = []
        while len(ix) < n:
            st = rng.integers(0, n - block); ix.extend(range(st, st + block))
        ix = np.array(ix[:n])
        for k in sigs:
            s = pd.Series(rets[k][ix])
            res[k]["sh"].append(M.sharpe(s, BPY)); res[k]["dd"].append(M.max_drawdown(s))
    for k in sigs:
        sh = np.array(res[k]["sh"]); dd = np.array(res[k]["dd"])
        print(f"  {k:<22} Sharpe 5/50/95: {np.percentile(sh,5):.2f}/{np.percentile(sh,50):.2f}/{np.percentile(sh,95):.2f}"
              f"   maxDD 50th: {np.percentile(dd,50)*100:.1f}%   P(Sh>1.18)={np.mean(sh>1.18)*100:.0f}%")


def main():
    df = load("BTCUSDT").loc[START:]
    sigs = {
        "locked": locked(df),
        "vol+8th": eighth(df, vote_volume(df)),
        "regime+8th": eighth(df, vote_volregime(df)),
    }
    walk(df, sigs, 365, "annual")
    walk(df, sigs, 180, "6-month")
    bootstrap(df, sigs)


if __name__ == "__main__":
    main()
