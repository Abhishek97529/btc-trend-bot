"""
What happens if we run trend_ensemble on FUTURES with leverage?

Models the two things spot backtests ignore and leverage makes lethal:
  1. LIQUIDATION on the intraday low (not just the close) -- a leveraged long is
     wiped when the adverse move reaches ~1/leverage. BTC has many -20%+ days.
  2. FUNDING COST paid on the leveraged notional every day you hold a perp long
     (~11.7%/yr measured earlier -> scales with leverage).

Usage:  python src/test_leverage.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_fetch import fetch_klines
from strategies_v2 import trend_ensemble
import metrics as M

BPY = 365
FEE, SLIP = 0.001, 0.0005
FUNDING_DAILY = 0.117 / 365     # ~11.7%/yr, measured from real Binance funding
MAINT = 0.005                    # ~0.5% maintenance margin


def simulate(df, pos, underlying, low_ret, leverage):
    """Bar-by-bar leveraged equity with intraday liquidation + funding."""
    equity = 1.0
    peak = 1.0
    maxdd = 0.0
    liquidated = False
    liq_date = None
    eq_curve = []
    prev_notional = 0.0
    for t in range(len(pos)):
        p = pos.iloc[t]
        # intraday liquidation check on the low (worst point of the day)
        if p > 0 and leverage * p * low_ret.iloc[t] <= -(1 - MAINT):
            equity = 0.0
            liquidated = True
            liq_date = df.index[t].date()
            eq_curve.append(0.0)
            break
        # close-to-close leveraged pnl
        day_pnl = leverage * p * underlying.iloc[t]
        funding = FUNDING_DAILY * leverage * p
        notional = leverage * p
        turn_cost = abs(notional - prev_notional) * (FEE + SLIP)
        prev_notional = notional
        equity *= (1 + day_pnl - funding - turn_cost)
        equity = max(equity, 0.0)
        eq_curve.append(equity)
        peak = max(peak, equity)
        if peak > 0:
            maxdd = min(maxdd, equity / peak - 1)
        if equity <= 0:
            liquidated = True
            liq_date = df.index[t].date()
            break
    return equity, maxdd, liquidated, liq_date, pd.Series(eq_curve, index=df.index[:len(eq_curve)])


def main():
    df = fetch_klines("BTCUSDT", "1d", "2017-08-01")
    df = df[~df.index.duplicated()].sort_index().loc["2018-06-01":]
    sig = trend_ensemble(df, threshold=0.5)
    pos = sig.shift(1).fillna(0.0)               # executed position
    underlying = df["close"].pct_change().fillna(0.0)
    low_ret = (df["low"] / df["close"].shift(1) - 1).fillna(0.0)  # intraday worst vs prev close

    print(f"\nStrategy on FUTURES with leverage | {df.index[0].date()} -> {df.index[-1].date()}")
    print(f"(spot 1x is the validated baseline; funding ~11.7%/yr on notional)\n")
    print(f"{'Leverage':<10}{'Final x':>12}{'CAGR':>9}{'MaxDD':>9}{'Liquidated?':>26}")
    print("-" * 66)
    for L in [1, 2, 3, 5, 10]:
        final, maxdd, liq, liqdate, curve = simulate(df, pos, underlying, low_ret, L)
        if liq:
            status = f"YES - WIPED OUT {liqdate}"
            cagr_s = "  -100%"
            finx = "0.00x"
        else:
            years = len(df) / BPY
            cagr = final ** (1 / years) - 1
            cagr_s = f"{cagr*100:6.1f}%"
            finx = f"{final:,.1f}x"
            status = "survived"
        print(f"{L}x{'':<8}{finx:>12}{cagr_s:>9}{maxdd*100:8.1f}%{status:>26}")

    # how many single days would liquidate a 5x long?
    worst_days = (low_ret[pos > 0] <= -0.19).sum()
    worst = low_ret[pos > 0].min()
    print(f"\nDays the strategy held a long AND BTC's intraday drop >=19% (5x kill zone): {worst_days}")
    print(f"Worst single-day intraday move while long: {worst*100:.1f}%")
    print("A 5x long is liquidated by a ~20% adverse move. BTC does that regularly.")


if __name__ == "__main__":
    main()
