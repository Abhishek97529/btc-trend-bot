"""
Optimize SG 2.0x. Its flaws: (a) Sharpe below buy&hold, (b) it AMPLIFIES crashes
(dot-com -78%, 2022 -46%) because the fixed 2x stays on until price finally loses
the 200-DMA -- so you eat the first leg down at 2x.

Fix = don't use a FIXED 2x. Scale leverage by volatility and trend quality, capped
at 2x, floored at 1x (CoinDCX min). When vol spikes (which is when crashes happen)
leverage falls automatically toward 1x -- BEFORE the 200-DMA breaks.

Candidates (all: gate on close>200-DMA for the boost, else 1x floor):
  SG_2x        baseline: fixed 2x up / 1x down.
  VOLCAP       up-lev = clip(TVOL/realized_vol, 1, 2). Calm bull -> 2x, turbulent -> ~1x.
  DUAL         2x only when close>200DMA AND >50DMA (faster de-risk), else 1x.
  VOLCAP_DUAL  VOLCAP but the boost also needs close>50DMA.
  VOLCAP_DD    VOLCAP + cut to 1x if underwater > DD_TRIG from a recent high (crash brake).

Honesty: optimized on a TRAIN half, reported on an unseen TEST half, both indices.
A real improvement shows up in TEST and on BOTH indices, not just in-sample.

Usage:  python src/us_swing_optimize.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "markets" / "us"

BPY = 252
COST, FUND = 0.0004, 0.08
MAINT = 0.005
CAP = 2.0
TVOL = {"ndx": 0.28, "spx": 0.20}     # vol target ~ each index's own long-run vol
DD_TRIG = 0.10                         # VOLCAP_DD: de-lever if >10% below recent peak


def load(which):
    f = DATA_DIR / ("data_ndx.csv" if which == "ndx" else "data_spx.csv")
    return pd.read_csv(f, parse_dates=["date"]).set_index("date").sort_index()


def lev_returns(pos, ret, low_ret):
    fund_d = FUND / BPY
    turnover = pos.diff().abs().fillna(pos.abs())
    borrow = (pos - 1.0).clip(lower=0.0)
    with np.errstate(divide="ignore"):
        thr = np.where(pos.values > 0, -(1 - MAINT) / np.where(pos.values > 0, pos.values, np.nan), -np.inf)
    liq = (low_ret.values <= thr) & (pos.values > 0)
    net = pos * ret - turnover * COST - borrow * fund_d
    net = net.where(~pd.Series(liq, index=pos.index), -1.0)
    return net.fillna(0.0), int(liq.sum())


def build(which):
    df = load(which)
    close = df["close"]
    ret = close.pct_change().fillna(0.0)
    low_ret = (df["low"] / close.shift(1) - 1).fillna(0.0)
    reg = (close > close.rolling(200).mean()).astype(float)
    reg_f = (close > close.rolling(50).mean()).astype(float)
    rvol = ret.rolling(20).std() * np.sqrt(BPY)
    tvol = TVOL[which]
    volcap = (tvol / rvol).clip(1.0, CAP)                 # 1..2 depending on turbulence
    peak = close.cummax()
    underwater = (close / peak - 1) < -DD_TRIG            # crash brake

    P = {}
    P["SG_2x"] = reg * CAP + (1 - reg) * 1.0
    P["VOLCAP"] = reg * volcap + (1 - reg) * 1.0
    P["DUAL"] = (reg * reg_f) * CAP + (1 - reg * reg_f) * 1.0
    P["VOLCAP_DUAL"] = (reg * reg_f) * volcap + (1 - reg * reg_f) * 1.0
    dd_lev = np.where(underwater, 1.0, volcap)
    P["VOLCAP_DD"] = reg * pd.Series(dd_lev, index=close.index) + (1 - reg) * 1.0
    P = {k: v.clip(1.0, CAP).shift(1).fillna(1.0) for k, v in P.items()}
    P["BH"] = pd.Series(1.0, index=close.index)

    rets = {k: lev_returns(v, ret, low_ret)[0] for k, v in P.items()}
    liqs = {k: lev_returns(v, ret, low_ret)[1] for k, v in P.items()}
    return df, rets, liqs


def row(r):
    return (M.cagr(r, BPY) * 100, M.sharpe(r, BPY), M.sortino(r, BPY),
            M.max_drawdown(r) * 100, M.calmar(r, BPY))


def show(rets, idx, title, order):
    print(f"\n{title}")
    print(f"  {'strat':<13}{'CAGR%':>8}{'Sharpe':>8}{'Sortino':>9}{'maxDD%':>9}{'Calmar':>8}")
    for k in order:
        r = rets[k].loc[idx]
        c, s, so, dd, ca = row(r)
        print(f"  {k:<13}{c:>8.1f}{s:>8.2f}{so:>9.2f}{dd:>9.1f}{ca:>8.2f}")


def main():
    order = ["BH", "SG_2x", "VOLCAP", "DUAL", "VOLCAP_DUAL", "VOLCAP_DD"]
    for which in ["ndx", "spx"]:
        name = "NASDAQ-100" if which == "ndx" else "S&P 500"
        df, rets, liqs = build(which)
        idx = df.index
        mid = idx[len(idx) // 2]

        print("\n" + "#" * 84)
        print(f"# {name}  ({idx.min().date()} -> {idx.max().date()})   TVOL={TVOL[which]:.0%}")
        print(f"# liquidations: " + ", ".join(f"{k}={liqs[k]}" for k in order if k != "BH"))
        print("#" * 84)

        train = idx[idx < mid]
        test = idx[idx >= mid]
        show(rets, train, f"TRAIN  ({train.min().date()} -> {train.max().date()}, in-sample)", order)
        show(rets, test, f"TEST   ({test.min().date()} -> {test.max().date()}, UNSEEN)", order)
        for w in [10, 5]:
            wi = idx[idx >= idx.max() - pd.Timedelta(days=int(w * 365.25))]
            show(rets, wi, f"LAST {w}y", order)

        # crash behaviour: total return through the worst windows
        print("\n  CRASH TEST -- total return (%) through historical drawdowns:")
        crashes = {"dotcom 2000-02": ("2000-03-01", "2002-10-01"),
                   "GFC 2008": ("2007-10-01", "2009-03-01"),
                   "COVID 2020": ("2020-02-15", "2020-04-01"),
                   "bear 2022": ("2022-01-01", "2022-12-31")}
        hdr = "    " + "".join(f"{k:>16}" for k in order)
        print(hdr)
        for cname, (a, b) in crashes.items():
            seg = idx[(idx >= a) & (idx <= b)]
            if len(seg) < 5:
                continue
            cells = "".join(f"{M.total_return(rets[k].loc[seg])*100:>16.1f}" for k in order)
            print(f"  {cname:<16}{cells}")


if __name__ == "__main__":
    main()
