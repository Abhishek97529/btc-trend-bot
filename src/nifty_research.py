"""Search for a Nifty strategy that beats buy-and-hold over the LAST 5 and 10 years.

Trend long/flat filters fail in the recent low-vol bull (see nifty_windows.py).
So here we test candidates with edge in a grinding bull market:

  BH            buy & hold (benchmark)
  LBH_1.3       constant 1.3x leverage, financing charged  (lever the bull)
  LBH_1.5       constant 1.5x leverage
  VT15          volatility target 15%, always long, lev in [0.5, 2.0]
  VT12          volatility target 12%, always long, lev in [0.5, 2.0]
  VT15_gate     VT15 but de-levered to 0.3x when below 200-SMA (crash brake)
  MR_RSI        buy-the-dip: long when RSI(3) < 15, exit when RSI(3) > 55, else flat
  MR_BB         buy-the-dip: long when close < lower Bollinger(20,2), exit at mid-band
  VT_MR         VT15 sizing, but add +0.5x when RSI(3) < 15 (lever into dips)

Same no-lookahead engine: signal on close[t], held t->t+1; 5 bps/side turnover;
6.5%/yr financing on leverage above 1x. Reports totRet / CAGR / vol / Sharpe /
maxDD / Calmar per window and flags whether each beats B&H on return AND Sharpe.

Usage:  python src/nifty_research.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M
ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "markets" / "india" / "data_nifty.csv"

BPY = 252
COST = 0.0005
RF_D = 0.065 / BPY


def load_close() -> pd.Series:
    df = pd.read_csv(DATA_FILE, parse_dates=["date"]).set_index("date").sort_index()
    return df["close"]


def net_returns(pos: pd.Series, ret: pd.Series) -> pd.Series:
    """pos = signed exposure held over each bar (already lagged). Costs + financing."""
    turnover = pos.diff().abs().fillna(pos.abs())
    borrow = (pos - 1.0).clip(lower=0.0)
    return (pos * ret - turnover * COST - borrow * RF_D).fillna(0.0)


def rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def state_machine(entry: pd.Series, exit_: pd.Series) -> pd.Series:
    """1 while in position: enter on `entry`, stay until `exit_`. Vectorized-ish."""
    pos = np.zeros(len(entry))
    inpos = False
    e = entry.values; x = exit_.values
    for i in range(len(pos)):
        if inpos:
            if x[i]:
                inpos = False
        else:
            if e[i]:
                inpos = True
        pos[i] = 1.0 if inpos else 0.0
    return pd.Series(pos, index=entry.index)


def build() -> dict[str, pd.Series]:
    close = load_close()
    ret = close.pct_change().fillna(0.0)
    rvol = ret.rolling(20).std() * np.sqrt(BPY)
    sma200 = close.rolling(200).mean()
    r3 = rsi(close, 3)

    out = {}
    out["BH"] = net_returns(pd.Series(1.0, index=close.index), ret)
    out["LBH_1.3"] = net_returns(pd.Series(1.3, index=close.index), ret)
    out["LBH_1.5"] = net_returns(pd.Series(1.5, index=close.index), ret)

    vt15 = (0.15 / rvol).clip(0.5, 2.0).shift(1).fillna(0.0)
    vt12 = (0.12 / rvol).clip(0.5, 2.0).shift(1).fillna(0.0)
    out["VT15"] = net_returns(vt15, ret)
    out["VT12"] = net_returns(vt12, ret)

    gate = np.where(close > sma200, 1.0, 0.3)
    vt15g = (pd.Series(gate, index=close.index) * (0.15 / rvol).clip(0.5, 2.0)).shift(1).fillna(0.0)
    out["VT15_gate"] = net_returns(vt15g, ret)

    mr_rsi = state_machine(r3 < 15, r3 > 55).shift(1).fillna(0.0)
    out["MR_RSI"] = net_returns(mr_rsi, ret)

    mid = close.rolling(20).mean()
    sd = close.rolling(20).std()
    lower = mid - 2 * sd
    mr_bb = state_machine(close < lower, close >= mid).shift(1).fillna(0.0)
    out["MR_BB"] = net_returns(mr_bb, ret)

    vt_mr = (vt15 + 0.5 * (r3 < 15).astype(float).shift(1).fillna(0.0)).clip(0.0, 2.0)
    out["VT_MR"] = net_returns(vt_mr, ret)

    # --- DIP-LEVERAGE OVERLAY: always 1x long (captures the bull like B&H),
    #     plus an extra leveraged tranche ONLY while a high-Sharpe dip-buy is on. ---
    dip_bb = state_machine(close < lower, close >= mid)          # BB(20,2) dip -> mid
    dip_rsi = state_machine(r3 < 15, r3 > 55)                    # RSI(3) oversold -> recover
    above200 = (close > sma200).astype(float)                   # crash brake

    dip_bb_10 = (1.0 + 1.0 * dip_bb).shift(1).fillna(1.0)
    dip_bb_05 = (1.0 + 0.5 * dip_bb).shift(1).fillna(1.0)
    dip_rsi_10 = (1.0 + 1.0 * dip_rsi).shift(1).fillna(1.0)
    # gated: only add dip leverage when regime is up (don't catch knives in a bear)
    dip_bb_gate = (1.0 + 1.0 * dip_bb * above200).shift(1).fillna(1.0)

    out["DIP_BB_1.0"] = net_returns(dip_bb_10, ret)
    out["DIP_BB_0.5"] = net_returns(dip_bb_05, ret)
    out["DIP_RSI_1.0"] = net_returns(dip_rsi_10, ret)
    out["DIP_BB_gate"] = net_returns(dip_bb_gate, ret)

    build.pos = {"VT15": vt15, "VT12": vt12, "VT15_gate": vt15g,
                 "MR_RSI": mr_rsi, "MR_BB": mr_bb, "VT_MR": vt_mr,
                 "DIP_BB_1.0": dip_bb_10, "DIP_BB_0.5": dip_bb_05,
                 "DIP_RSI_1.0": dip_rsi_10, "DIP_BB_gate": dip_bb_gate}
    return out


def window(r: pd.Series, years):
    if years is not None:
        r = r[r.index >= r.index.max() - pd.Timedelta(days=int(years * 365.25))]
    return {"totRet%": M.total_return(r) * 100, "CAGR%": M.cagr(r, BPY) * 100,
            "vol%": M.ann_vol(r, BPY) * 100, "sharpe": M.sharpe(r, BPY),
            "maxDD%": M.max_drawdown(r) * 100, "calmar": M.calmar(r, BPY)}


def main() -> None:
    rets = build()
    order = list(rets.keys())
    for wname, yrs in [("full", None), ("10-year", 10.0), ("5-year", 5.0), ("3-year", 3.0)]:
        print("\n" + "=" * 84)
        print(f"WINDOW: {wname}")
        print("=" * 84)
        tbl = pd.DataFrame({n: window(rets[n], yrs) for n in order}).T.round(2)
        bh_r, bh_s = tbl.loc["BH", "totRet%"], tbl.loc["BH", "sharpe"]
        tbl["beatRet"] = np.where(tbl["totRet%"] > bh_r, "Y", "-")
        tbl["beatShrp"] = np.where(tbl["sharpe"] > bh_s, "Y", "-")
        tbl["BEATS"] = np.where((tbl["totRet%"] > bh_r) & (tbl["sharpe"] > bh_s), "** ", "")
        print(tbl.to_string())


if __name__ == "__main__":
    main()
