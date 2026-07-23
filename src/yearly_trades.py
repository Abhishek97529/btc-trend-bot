"""
Yearly trade counts for the locked trend_ensemble strategy, two ways:
  - Round-trip episodes  : how many times we opened a fresh position that year
                           (entry = exposure goes 0 -> >0). The "real" trade count.
  - Rebalance orders     : every day the executed position CHANGED (incl. resizing
                           inside a position). This is what TradingView tallies.

Usage:  python src/yearly_trades.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_fetch import fetch_klines
from strategies_v2 import trend_ensemble
from backtest import run_backtest

FEE, SLIP = 0.001, 0.0005


def main():
    df = fetch_klines("BTCUSDT", "1d", "2017-08-01")
    df = df[~df.index.duplicated()].sort_index().loc["2018-06-01":]
    sig = trend_ensemble(df, threshold=0.5)
    res = run_backtest(df, sig, fee=FEE, slippage=SLIP, bars_per_year=365)
    pos = res.position

    in_mkt = (pos.abs() > 1e-9)
    prev = in_mkt.shift(1, fill_value=False)             # keeps bool dtype (no object cast)
    entries = in_mkt & ~prev                             # 0 -> in-market = new episode
    exits = ~in_mkt & prev                               # in-market -> 0 = closed episode
    rebomoves = pos.diff().fillna(pos).abs() > 1e-9      # any change in executed position

    yr = pd.DataFrame({
        "new_trades": entries.groupby(entries.index.year).sum(),
        "closed_trades": exits.groupby(exits.index.year).sum(),
        "rebalance_orders": rebomoves.groupby(rebomoves.index.year).sum(),
        "days_in_market": in_mkt.groupby(in_mkt.index.year).sum(),
    })
    yr.index.name = "year"

    print(f"\nYearly trade activity | {df.index[0].date()} -> {df.index[-1].date()}")
    print("(new_trades = fresh positions opened; rebalance_orders = TradingView-style)\n")
    print(yr.to_string())
    print("\nTOTALS:")
    print(f"  Round-trip trades opened : {int(yr['new_trades'].sum())}")
    print(f"  Rebalance orders         : {int(yr['rebalance_orders'].sum())}")
    print(f"  Avg round-trip trades/yr : {yr['new_trades'].mean():.1f}")
    print(f"  Avg rebalance orders/yr  : {yr['rebalance_orders'].mean():.1f}")


if __name__ == "__main__":
    main()
