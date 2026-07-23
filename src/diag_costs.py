"""
Diagnostic: is our reported ~2,326% GROSS or NET of costs? And how much of the
gap vs the independent ChatGPT run is data/window rather than costs?

Prints, on OUR data, both the gross (no-cost) and net (with-cost) equity, the
turnover, the cost drag, plus the exact window and bar count so we can line it up
against the independent reproduction (net 2,046% / gross 2,271%, 2,974 bars,
2018-06-01 -> 2026-07-22).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_fetch import fetch_klines
from strategies_v2 import trend_ensemble

FEE, SLIP = 0.001, 0.0005


def run(df, tag):
    close = df["close"]
    bar_ret = close.pct_change().fillna(0.0)
    target = trend_ensemble(df, threshold=0.5)
    executed = target.reindex(df.index).ffill().fillna(0.0).clip(0.0, 1.0).shift(1).fillna(0.0)
    dpos = executed.diff().abs().fillna(executed.abs())
    cost = dpos * (FEE + SLIP)

    gross_ret = executed * bar_ret
    net_ret = gross_ret - cost
    gross_eq = (1 + gross_ret).cumprod().iloc[-1]
    net_eq = (1 + net_ret).cumprod().iloc[-1]

    print(f"\n=== {tag} ===")
    print(f"  window            : {df.index[0].date()} -> {df.index[-1].date()}")
    print(f"  bars              : {len(df)}")
    print(f"  turnover (sum|dp|): {dpos.sum():.4f}")
    print(f"  cost drag (sum)   : {cost.sum()*100:.4f}% of equity (arithmetic)")
    print(f"  GROSS final mult  : {gross_eq:.4f}x   ({(gross_eq-1)*100:,.2f}%)")
    print(f"  NET   final mult  : {net_eq:.4f}x   ({(net_eq-1)*100:,.2f}%)")
    print(f"  cost impact       : {(1 - net_eq/gross_eq)*100:.2f}% of final equity")
    return gross_eq, net_eq


def main():
    df = fetch_klines("BTCUSDT", "1d", "2017-08-01")
    df = df[~df.index.duplicated()].sort_index()

    # exactly what tv_report uses (open-ended tail -> includes our last cached bar)
    a = df.loc["2018-06-01":]
    run(a, "OUR default window (tv_report): 2018-06-01 -> last cached bar")

    # match ChatGPT's window exactly: end at 2026-07-22 (exclude in-progress 07-23)
    b = df.loc["2018-06-01":"2026-07-22"]
    run(b, "MATCHED to ChatGPT window: 2018-06-01 -> 2026-07-22")

    print("\nChatGPT reported:  NET 2046.36% (21.4636x)   GROSS 2270.84% (23.7084x)")
    print("If OUR net ~ their gross, we have a cost bug. If OUR net ~ their net, it's data/window.")


if __name__ == "__main__":
    main()
