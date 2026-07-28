"""Trailing-window comparison of Nifty strategies vs buy-and-hold.

Reuses build() from nifty_strategy (same no-lookahead engine, costs, financing).
Reports full / 10yr / 5yr / 3yr metrics and per-window win/lose vs B&H, so we can
see explicitly whether each strategy beats buy-and-hold over the last 5 and 10 years.

Usage:  python src/nifty_windows.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M
from nifty_strategy import build, BPY


def window_metrics(r: pd.Series, years: float | None) -> dict:
    if years is not None:
        end = r.index.max()
        start = end - pd.Timedelta(days=int(years * 365.25))
        r = r[r.index >= start]
    return {
        "totRet%": M.total_return(r) * 100,
        "CAGR%": M.cagr(r, BPY) * 100,
        "vol%": M.ann_vol(r, BPY) * 100,
        "sharpe": M.sharpe(r, BPY),
        "maxDD%": M.max_drawdown(r) * 100,
        "calmar": M.calmar(r, BPY),
    }


def main() -> None:
    rets = build()
    windows = [("full (18.9y)", None), ("10-year", 10.0), ("5-year", 5.0), ("3-year", 3.0)]
    order = ["BH", "REG", "REG100", "LEV", "EQRISK"]

    for wname, yrs in windows:
        print("\n" + "=" * 78)
        print(f"WINDOW: {wname}")
        print("=" * 78)
        rows = {name: window_metrics(rets[name], yrs) for name in order}
        tbl = pd.DataFrame(rows).T.round(2)
        # beat B&H flags on total return + Sharpe
        bh_ret = tbl.loc["BH", "totRet%"]
        bh_shp = tbl.loc["BH", "sharpe"]
        tbl["beatRet"] = np.where(tbl["totRet%"] > bh_ret, "Y", "-")
        tbl["beatSharpe"] = np.where(tbl["sharpe"] > bh_shp, "Y", "-")
        print(tbl.to_string())


if __name__ == "__main__":
    main()
