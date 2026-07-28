"""
Search: over the LAST 10 YEARS only, what swing/positional overlay on the US-index
perp genuinely beats buy-and-hold -- on return AND risk-adjusted (Sharpe/Calmar)?

The 10-yr NASDAQ/S&P bull is hard to beat with gating (proved in us_swing_10yr.py:
DIPLEV only ties, SWING loses). So we grid a menu of realistic swing overlays and
rank them. Same honest perp engine (4bp/side, 8%/yr funding on lev>1, liquidation).

Usage:  python src/us_swing_search.py [ndx|spx] [years]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M
from us_swing_10yr import (BPY, MAX_LEV, VOL_TARGET, SLOW, VOL_LB, BB_N, BB_K,
                           load, lev_returns, dip_state)


def build_variants(df):
    close, low = df["close"], df["low"]
    ret = close.pct_change().fillna(0.0)
    low_ret = (low / close.shift(1) - 1).fillna(0.0)
    sma_s = close.rolling(SLOW).mean()
    sma_f = close.rolling(50).mean()
    rvol = ret.rolling(VOL_LB).std() * np.sqrt(BPY)
    reg = (close > sma_s).astype(float)
    reg_f = (close > sma_f).astype(float)
    dip = dip_state(close)

    V = {}
    V["BH"] = pd.Series(1.0, index=close.index)
    # constant leverage buy&hold (pure beta dial)
    V["LEV_1.3x"] = pd.Series(1.3, index=close.index)
    V["LEV_1.5x"] = pd.Series(1.5, index=close.index)
    # regime-gated leverage (avoid bears, leverage bulls)
    V["REG_1.5x"] = (reg * 1.5)
    V["REG_2.0x"] = (reg * 2.0)
    # partial de-risk instead of full exit (1x up, 0.5x down)
    V["SOFTGATE"] = (reg * 1.0 + (1 - reg) * 0.5)
    # leveraged softgate: lever the BEST-Sharpe structure up instead of plain B&H
    V["SOFTGATE_1.5x"] = (reg * 1.5 + (1 - reg) * 0.75)
    V["SOFTGATE_2.0x"] = (reg * 2.0 + (1 - reg) * 1.0)
    # softgate core + dip boost, capped 2x
    V["SOFTGATE_DIP"] = (reg * (1.4 + 0.6 * dip) + (1 - reg) * 0.7)
    # dip-leverage (the base winner)
    V["DIPLEV"] = (1.0 + 1.0 * dip * reg)
    # dip-leverage on a 1.3x core (lever the whole thing a touch)
    V["DIPLEV_1.3core"] = (1.3 + 0.7 * dip * reg)
    # regime 1.3x core + dip to 2x, de-risk to 0.5x in downtrend
    V["HYBRID"] = (reg * (1.3 + 0.7 * dip) + (1 - reg) * 0.5)
    # vol-target in uptrend (floor 1x, cap 2x) + dip boost, flat in bear
    vt = (VOL_TARGET / rvol).clip(1.0, MAX_LEV)
    V["VT_TREND"] = (reg * (vt))
    V["VT_DIP"] = (reg * (vt + 0.5 * dip)).clip(0, MAX_LEV)

    # lag + cap all
    out = {}
    for k, p in V.items():
        out[k] = p.clip(0.0, MAX_LEV).shift(1).fillna(1.0 if k == "BH" else 0.0)
    return out, ret, low_ret


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "ndx"
    years = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    name = "NASDAQ-100" if which == "ndx" else "S&P 500"
    df = load(which)
    variants, ret, low_ret = build_variants(df)

    cutoff = df.index.max() - pd.Timedelta(days=int(years * 365.25))
    mask = df.index >= cutoff

    rows = {}
    liqcount = {}
    for k, pos in variants.items():
        r, _ = lev_returns(pos, ret, low_ret)
        rw = r[mask]
        liqcount[k] = int((rw <= -0.999).sum())
        rows[k] = {"CAGR%": M.cagr(rw, BPY) * 100, "totRet%": M.total_return(rw) * 100,
                   "vol%": M.ann_vol(rw, BPY) * 100, "Sharpe": M.sharpe(rw, BPY),
                   "Sortino": M.sortino(rw, BPY), "maxDD%": M.max_drawdown(rw) * 100,
                   "Calmar": M.calmar(rw, BPY), "liq": liqcount[k]}
    tbl = pd.DataFrame(rows).T
    bh = tbl.loc["BH"]
    tbl["beatRet"] = np.where(tbl["totRet%"] > bh["totRet%"], "Y", "-")
    tbl["beatShrp"] = np.where(tbl["Sharpe"] > bh["Sharpe"], "Y", "-")
    tbl["beatCalmar"] = np.where(tbl["Calmar"] > bh["Calmar"], "Y", "-")

    lo, hi = df.index[mask].min().date(), df.index[mask].max().date()
    print("=" * 100)
    print(f"{name}  --  LAST {years:.0f} YEARS ({lo} -> {hi}) -- overlay search vs Buy&Hold")
    print("=" * 100)
    tbl = tbl.sort_values("Sharpe", ascending=False)
    print(tbl.round(2).to_string())
    print("\nBeats B&H on BOTH return and Sharpe:")
    win = tbl[(tbl["beatRet"] == "Y") & (tbl["beatShrp"] == "Y")]
    print("  " + (", ".join(win.index) if len(win) else "NONE"))


if __name__ == "__main__":
    main()
