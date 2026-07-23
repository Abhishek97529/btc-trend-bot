"""
Full-metrics leveraged-futures backtest of trend_ensemble at 2x and 3x.

Same signals, same dates as the validated spot (1x) strategy, but now on a
perpetual-futures model that includes the things spot ignores:
  - LIQUIDATION on the intraday low (a long dies when leverage*adverse_move
    reaches ~-(1 - maintenance)).
  - FUNDING paid daily on the leveraged notional (~11.7%/yr measured -> x leverage).
  - Fees + slippage charged on the leveraged turnover.

Produces the same TradingView-style metric suite we use for spot so it's an
apples-to-apples comparison.

Usage:  python src/test_leverage_full.py
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
FUNDING_DAILY = 0.117 / 365      # ~11.7%/yr on notional (measured from real Binance funding)
MAINT = 0.005                     # ~0.5% maintenance margin


def leveraged_returns(df, pos, underlying, low_ret, leverage):
    """Return the daily net return series for a leveraged long/flat perp.

    Once liquidated, all subsequent returns are 0 (account is dead)."""
    rets = np.zeros(len(pos))
    prev_notional = 0.0
    liq_idx = None
    for t in range(len(pos)):
        p = pos.iloc[t]
        # intraday liquidation on the low: leverage * adverse move wipes margin
        if p > 0 and leverage * p * low_ret.iloc[t] <= -(1 - MAINT):
            rets[t] = -1.0                       # lose everything this bar
            liq_idx = t
            break
        day_pnl = leverage * p * underlying.iloc[t]
        funding = FUNDING_DAILY * leverage * p
        notional = leverage * p
        turn_cost = abs(notional - prev_notional) * (FEE + SLIP)
        prev_notional = notional
        rets[t] = day_pnl - funding - turn_cost
    s = pd.Series(rets, index=df.index)
    return s, liq_idx


def count_trades(pos):
    """Contiguous in-market episodes = round-trip trades (same convention as spot)."""
    inmkt = (pos > 0).astype(int)
    entries = ((inmkt == 1) & (inmkt.shift(1).fillna(0) == 0)).sum()
    return int(entries)


def show(ret, tag, trades=None, expo=None):
    s = M.summary(ret, BPY)
    line = (f"{tag:<32} ret={s['total_return']*100:10.1f}%  cagr={s['cagr']*100:6.1f}%  "
            f"sharpe={s['sharpe']:5.2f}  sortino={s['sortino']:5.2f}  "
            f"maxDD={s['max_drawdown']*100:6.1f}%  calmar={s['calmar']:5.2f}")
    print(line)
    return s


def main():
    df = fetch_klines("BTCUSDT", "1d", "2017-08-01")
    df = df[~df.index.duplicated()].sort_index().loc["2018-06-01":]
    sig = trend_ensemble(df, threshold=0.5)
    pos = sig.shift(1).fillna(0.0)
    underlying = df["close"].pct_change().fillna(0.0)
    low_ret = (df["low"] / df["close"].shift(1) - 1).fillna(0.0)

    print(f"\nFUTURES leverage comparison | {df.index[0].date()} -> {df.index[-1].date()}  "
          f"({len(df)} daily bars)")
    print("Model: intraday liquidation + funding ~11.7%/yr x leverage + fees on notional\n")

    # benchmarks
    bh = underlying
    show(bh, "BTC buy & hold (1x)")

    for L in [1, 2, 3]:
        ret, liq = leveraged_returns(df, pos, underlying, low_ret, L)
        tag = f"trend_ensemble {L}x futures"
        s = show(ret, tag)
        if liq is not None:
            print(f"    ^ LIQUIDATED on {df.index[liq].date()} -> account to zero, dead thereafter")

    # detailed stats for the survivors
    print("\n" + "=" * 96)
    print("DETAIL: worst drawdowns and yearly returns for 2x and 3x")
    print("=" * 96)
    for L in [2, 3]:
        ret, liq = leveraged_returns(df, pos, underlying, low_ret, L)
        eq = (1 + ret).cumprod()
        dd = eq / eq.cummax() - 1
        print(f"\n--- {L}x futures ---")
        print(f"  Max drawdown:        {dd.min()*100:6.1f}%   (on {dd.idxmin().date()})")
        print(f"  Days spent >50% down: {(dd < -0.50).sum()}")
        print(f"  Days spent >70% down: {(dd < -0.70).sum()}")
        yr = ret.groupby(ret.index.year).apply(lambda r: (1 + r).prod() - 1)
        print("  Year-by-year return:")
        for y, v in yr.items():
            bar = "#" * min(int(abs(v) * 20), 40)
            sign = "+" if v >= 0 else "-"
            print(f"    {y}: {sign}{abs(v)*100:7.1f}%  {bar}")

    print("\nReminder: 5x liquidates (2021-01-11), 10x liquidates (2019-05-17).")
    print("Spot 1x remains the only version that was robustness-tested end to end.")


if __name__ == "__main__":
    main()
