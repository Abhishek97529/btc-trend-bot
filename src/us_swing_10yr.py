"""
US-index perp SWING/POSITIONAL strategy -- LAST 10 YEARS ONLY, with a full
month-on-month return matrix and year-on-year returns.

Restricting to the last 10 years is deliberate per the request: this is the regime
you would actually trade (post-2015 NASDAQ/S&P), so leverage is calibrated to it
rather than to the 1987/2000/2008 crashes.

Timeframe: DAILY bars. It is a SWING/POSITIONAL strategy -- signals computed on the
daily close, positions held for days-to-weeks (avg hold reported below), not intraday.

Strategy DIPLEV (the cross-asset winner):
  - base 1x long the perp at all times,
  - +1 extra unit (-> 2x) when close < lower Bollinger(20, 2s) AND close > 200-DMA,
  - drop the extra unit when close reclaims the 20-day mean.
Strategy SWING (crash-gated, more aggressive in this benign 10-yr window):
  - only in when close > 200-DMA; vol-target the exposure (floor 0.75x) + dip boost,
    capped at 2x; flat in downtrends.

Perp modeling: 4 bps/side cost, 8%/yr funding on leverage>1x, liquidation on daily low.
No lookahead (signal close[t], held t->t+1).

Usage:  python src/us_swing_10yr.py [ndx|spx] [years]
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
REPORT_DIR = ROOT / "reports" / "legacy-markets" / "us"

BPY = 252
COST = 0.0004
FUND_ANNUAL = 0.08
FUND_D = FUND_ANNUAL / BPY
MAINT = 0.005
MAX_LEV = 2.0
VOL_TARGET = 0.20
SLOW, FAST, VOL_LB, BB_N, BB_K = 200, 50, 20, 20, 2.0


def load(which: str) -> pd.DataFrame:
    f = DATA_DIR / ("data_ndx.csv" if which == "ndx" else "data_spx.csv")
    return pd.read_csv(f, parse_dates=["date"]).set_index("date").sort_index()


def lev_returns(pos, ret, low_ret):
    turnover = pos.diff().abs().fillna(pos.abs())
    borrow = (pos - 1.0).clip(lower=0.0)
    with np.errstate(divide="ignore"):
        liq_thresh = np.where(pos > 0, -(1 - MAINT) / pos.replace(0, np.nan), -np.inf)
    liq = (low_ret.values <= liq_thresh) & (pos.values > 0)
    net = pos * ret - turnover * COST - borrow * FUND_D
    net = net.where(~liq, -1.0)
    return net.fillna(0.0), int(liq.sum())


def dip_state(close, n=BB_N, k=BB_K):
    mid = close.rolling(n).mean(); sd = close.rolling(n).std()
    lower = mid - k * sd
    e = (close < lower).values; x = (close >= mid).values
    out = np.zeros(len(close)); inpos = False
    for i in range(len(close)):
        inpos = (inpos and not x[i]) or (not inpos and e[i])
        out[i] = 1.0 if inpos else 0.0
    return pd.Series(out, index=close.index)


def build(which: str):
    """Positions built on FULL history (200-DMA etc. warm), sliced to window later."""
    df = load(which)
    close, low = df["close"], df["low"]
    ret = close.pct_change().fillna(0.0)
    low_ret = (low / close.shift(1) - 1).fillna(0.0)

    sma_s = close.rolling(SLOW).mean()
    regime = (close > sma_s).astype(float)
    dip = dip_state(close)

    pos_bh = pd.Series(1.0, index=close.index)
    # 1x FLOOR (CoinDCX min leverage = 1x): lever up in the uptrend, drop back to
    # exactly 1x below the 200-DMA -- never below 1x, never flat.
    pos_soft15 = (regime * 1.5 + (1 - regime) * 1.0).shift(1).fillna(1.0)   # 1.5x up / 1x down
    pos_soft20 = (regime * 2.0 + (1 - regime) * 1.0).shift(1).fillna(1.0)   # 2x  up / 1x down
    # DIPLEV kept for reference (1x + dip to 2x, gated by 200-DMA).
    pos_dip = (1.0 + 1.0 * dip * regime).shift(1).fillna(1.0)

    return df, ret, low_ret, {"BH": pos_bh, "SG_1.5x": pos_soft15,
                              "SG_2.0x": pos_soft20, "DIPLEV": pos_dip}


def hold_stats(pos):
    """Avg consecutive-invested run length (bars) and % of days invested."""
    invested = (pos.abs() > 1e-9)
    runs = []; run = 0
    for v in invested.values:
        if v:
            run += 1
        elif run:
            runs.append(run); run = 0
    if run:
        runs.append(run)
    avg_hold = np.mean(runs) if runs else 0.0
    return avg_hold, invested.mean() * 100


def monthly_matrix(r, label):
    m = r.groupby([r.index.year, r.index.month]).apply(lambda x: M.total_return(x) * 100)
    m.index.names = ["year", "month"]
    mat = m.unstack("month")
    mat.columns = [pd.Timestamp(2000, c, 1).strftime("%b") for c in mat.columns]
    yearly = r.groupby(r.index.year).apply(lambda x: M.total_return(x) * 100)
    mat["YEAR"] = yearly
    print(f"\n### MONTH-ON-MONTH RETURN (%)  --  {label}")
    print(mat.round(1).to_string(na_rep="   ."))
    pos = m[m.notna()]
    print(f"  months positive: {100*(pos>0).mean():.0f}%  |  avg month {pos.mean():.2f}%  |  "
          f"best {pos.max():.1f}%  worst {pos.min():.1f}%")


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "ndx"
    years = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    name = "NASDAQ-100" if which == "ndx" else "S&P 500"
    df, ret, low_ret, positions = build(which)

    cutoff = df.index.max() - pd.Timedelta(days=int(years * 365.25))
    mask = df.index >= cutoff
    order = ["BH", "SG_1.5x", "SG_2.0x", "DIPLEV"]

    rets, liqs = {}, {}
    for n in order:
        r, _ = lev_returns(positions[n], ret, low_ret)
        rw = r[mask]
        rets[n] = rw
        liqs[n] = int((rw <= -0.999).sum())

    lo, hi = df.index[mask].min().date(), df.index[mask].max().date()
    print("=" * 104)
    print(f"{name} SWING/POSITIONAL PERP STRATEGY  --  LAST {years:.0f} YEARS  ({lo} -> {hi})")
    print("timeframe: DAILY bars, positions held days->weeks | cost 4bp/side | "
          "funding 8%/yr on lev>1 | cap 2x | liquidation modeled")
    print("=" * 104)

    print(f"\n{'strategy':<9}{'CAGR%':>8}{'totRet%':>10}{'vol%':>7}{'Sharpe':>8}{'Sortino':>8}"
          f"{'maxDD%':>8}{'Calmar':>8}{'avgHold':>9}{'expo%':>7}")
    print("-" * 96)
    for n in order:
        avg_hold, expo = hold_stats(positions[n][mask])
        r = rets[n]
        print(f"{n:<9}{M.cagr(r,BPY)*100:>8.1f}{M.total_return(r)*100:>10.0f}{M.ann_vol(r,BPY)*100:>7.1f}"
              f"{M.sharpe(r,BPY):>8.2f}{M.sortino(r,BPY):>8.2f}{M.max_drawdown(r)*100:>8.1f}"
              f"{M.calmar(r,BPY):>8.2f}{avg_hold:>8.0f}d{expo:>7.0f}")
    print(f"\ngrowth of $1000 over {years:.0f}y:  "
          + "   ".join(f"{n} ${1000*(1+M.total_return(rets[n])):,.0f}" for n in order))
    print("liquidations in window: " + ", ".join(f"{n}={liqs[n]}" for n in order))

    print("\n### YEAR-ON-YEAR TOTAL RETURN (%)")
    yy = pd.DataFrame({n: rets[n].groupby(rets[n].index.year).apply(lambda x: M.total_return(x)*100)
                       for n in order}).round(1)
    yy["SG1.5-BH"] = (yy["SG_1.5x"] - yy["BH"]).round(1)
    yy["SG2.0-BH"] = (yy["SG_2.0x"] - yy["BH"]).round(1)
    print(yy.to_string())
    for n in ["SG_1.5x", "SG_2.0x", "DIPLEV"]:
        print(f"{n} beat B&H in {(yy[n]>yy['BH']).sum()}/{len(yy)} years")

    for n in order:
        monthly_matrix(rets[n], f"{name}  {n}")

    r = rets["DIPLEV"]
    mm = r.groupby([r.index.year, r.index.month]).apply(lambda x: M.total_return(x)*100).unstack()
    out = REPORT_DIR / f"swing_monthly_{which}.csv"
    mm.to_csv(out)
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
