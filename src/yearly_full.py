"""
Year-on-year returns (strategy vs buy & hold) alongside trade activity.

Usage:  python src/yearly_full.py
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

    net = res.returns
    bh = df["close"].pct_change().fillna(0.0)
    pos = res.position

    in_mkt = pos.abs() > 1e-9
    prev = in_mkt.shift(1, fill_value=False)
    entries = in_mkt & ~prev
    rebmoves = pos.diff().fillna(pos).abs() > 1e-9

    by = lambda s: s.groupby(s.index.year)
    out = pd.DataFrame({
        "strategy_%": by(net).apply(lambda g: round(((1 + g).prod() - 1) * 100, 1)),
        "buy_hold_%": by(bh).apply(lambda g: round(((1 + g).prod() - 1) * 100, 1)),
        "trades": by(entries).sum().astype(int),
        "rebal_orders": by(rebmoves).sum().astype(int),
        "days_in_mkt": by(in_mkt).sum().astype(int),
    })
    out["outperf_%"] = (out["strategy_%"] - out["buy_hold_%"]).round(1)
    out = out[["strategy_%", "buy_hold_%", "outperf_%", "trades", "rebal_orders", "days_in_mkt"]]
    out.index.name = "year"

    print(f"\nYear-on-year returns + trades | {df.index[0].date()} -> {df.index[-1].date()}\n")
    print(out.to_string())

    tot_strat = (1 + net).prod() - 1
    tot_bh = (1 + bh).prod() - 1
    print(f"\nFULL PERIOD:  strategy {tot_strat*100:,.1f}%   buy&hold {tot_bh*100:,.1f}%"
          f"   trades {int(entries.sum())}   rebal_orders {int(rebmoves.sum())}")
    wins = (out["strategy_%"] > 0).sum()
    beat = (out["strategy_%"] > out["buy_hold_%"]).sum()
    print(f"Positive years: {wins}/{len(out)}   |   Years beating buy&hold: {beat}/{len(out)}")


if __name__ == "__main__":
    main()
