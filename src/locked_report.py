"""
DETAILED report for the LOCKED SPOT strategy (trend_ensemble, threshold 0.5).

Prints, straight from src/config.py (no tuning):
  * every locked parameter,
  * headline full-cycle metrics,
  * YEAR-OVER-YEAR returns vs buy & hold, with days-in-market / trades / drawdown,
  * a TRADE-BY-TRADE holding log (entry date -> exit date, days held, return %).

A "position spell" = a contiguous stretch where the executed position > 0. Because the
ensemble sizes fractionally, exposure can change WITHIN a spell; the spell return is the
compounded strategy return (net of costs) over those in-market bars, and "days held" is
the number of calendar days from first to last in-market bar of that spell.

Usage:  python src/locked_report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C
from strategies_v2 import trend_ensemble
from backtest import run_backtest
import metrics as M

DATA = Path(__file__).resolve().parent.parent / "data" / "BTCUSDT_1d_2017-08-01_2026-07-23.parquet"


def load():
    df = pd.read_parquet(DATA)
    return df[~df.index.duplicated()].sort_index()


def spells(pos: pd.Series, strat_ret: pd.Series, close: pd.Series):
    """Contiguous in-market (position>0) spells -> entry/exit/days/return rows."""
    inmkt = (pos.values > 1e-9)
    idx = pos.index
    rows = []
    t = 0
    n = len(idx)
    while t < n:
        if not inmkt[t]:
            t += 1
            continue
        s = t
        while t < n and inmkt[t]:
            t += 1
        e = t - 1                                   # last in-market bar
        seg = strat_ret.iloc[s:e + 1]
        spell_ret = (1 + seg).prod() - 1
        rows.append({
            "entry": idx[s].date(),
            "exit": idx[e].date(),
            "days_held": (idx[e] - idx[s]).days + 1,
            "bars": e - s + 1,
            "avg_expo": float(pos.iloc[s:e + 1].mean()),
            "px_in": round(float(close.iloc[s]), 1),
            "px_out": round(float(close.iloc[e]), 1),
            "ret_%": round(spell_ret * 100, 1),
        })
    return pd.DataFrame(rows)


def per_year(df, ret, pos):
    bh = df["close"].pct_change().fillna(0.0)
    rows = []
    for yr, g in ret.groupby(ret.index.year):
        p = pos.loc[g.index]
        rows.append({
            "year": yr,
            "strat_%": round((((1 + g).prod()) - 1) * 100, 1),
            "buyhold_%": round((((1 + bh.loc[g.index]).prod()) - 1) * 100, 1),
            "days_in_mkt": int((p.values > 1e-9).sum()),
            "days_total": len(g),
            "in_mkt_%": round((p.values > 1e-9).mean() * 100, 0),
            "trades": int((p.diff().fillna(p.iloc[0]) != 0).sum()),
            "maxDD_%": round(M.max_drawdown(g) * 100, 1),
            "sharpe": round(M.sharpe(g, C.BARS_PER_YEAR), 2),
        })
    return pd.DataFrame(rows)


def main():
    full = load()
    sig = trend_ensemble(full, C.THRESHOLD).loc[C.BACKTEST_START:]
    df = full.loc[C.BACKTEST_START:]
    res = run_backtest(df, sig, C.FEE, C.SLIPPAGE, C.BARS_PER_YEAR)
    ret, pos = res.returns, res.position

    print("\n" + "=" * 78)
    print("LOCKED SPOT STRATEGY  —  DETAILED REPORT")
    print("=" * 78)
    print(f"  Instrument      : {C.SYMBOL} {C.MARKET} {C.TIMEFRAME}  (long/flat, NO leverage/shorts)")
    print(f"  Strategy        : {C.STRATEGY}  (agreement gate = {C.THRESHOLD})")
    print(f"  7 trend votes   : close>SMA{C.SMA_WINDOWS}, EMA{C.EMA_PAIRS[0]}, "
          f"EMA{C.EMA_PAIRS[1]}, Donchian({C.DONCHIAN_WINDOW}), ROC({C.MOMENTUM_LOOKBACK})>0")
    print(f"  Warmup          : {C.WARMUP_DAYS} days")
    print(f"  Costs / side    : fee {C.FEE*100:.2f}% + slippage {C.SLIPPAGE*100:.3f}%  (on turnover)")
    print(f"  Capital / bpy   : {C.INITIAL_CAPITAL:,.0f}  |  {C.BARS_PER_YEAR} bars/yr")
    print(f"  Sample          : {df.index[0].date()} -> {df.index[-1].date()} ({len(df)} daily bars)")

    s = M.summary(ret, C.BARS_PER_YEAR, res.trades, res.gross_exposure_time)
    bh = df["close"].pct_change().fillna(0.0)
    print("\n" + "-" * 78)
    print("HEADLINE (full cycle, net of costs)")
    print("-" * 78)
    print(f"  Net profit      : {M.total_return(ret)*100:,.1f}%   "
          f"(Rs {C.INITIAL_CAPITAL*(1+M.total_return(ret)):,.0f} from Rs {C.INITIAL_CAPITAL:,.0f})")
    print(f"  Buy & hold      : {M.total_return(bh)*100:,.1f}%")
    print(f"  CAGR            : {s['cagr']*100:.2f}%      Ann.vol: {s['ann_vol']*100:.1f}%")
    print(f"  Sharpe          : {s['sharpe']:.2f}         Sortino: {s['sortino']:.2f}")
    print(f"  Max drawdown    : {s['max_drawdown']*100:.2f}%     Calmar: {s['calmar']:.2f}")
    print(f"  Time in market  : {s['exposure']*100:.1f}%       Rebalances: {res.trades}")

    print("\n" + "=" * 78)
    print("YEAR-OVER-YEAR")
    print("=" * 78)
    print(per_year(df, ret, pos).to_string(index=False))

    print("\n" + "=" * 78)
    print("TRADE-BY-TRADE HOLDING LOG  (in-market spells)")
    print("=" * 78)
    sp = spells(pos, ret, df["close"])
    print(sp.to_string(index=False))
    wins = sp[sp["ret_%"] > 0]
    print(f"\n  spells: {len(sp)}   winners: {len(wins)} ({len(wins)/len(sp)*100:.0f}%)   "
          f"avg hold: {sp['days_held'].mean():.0f}d   median hold: {sp['days_held'].median():.0f}d   "
          f"longest: {sp['days_held'].max()}d")
    print(f"  best spell: +{sp['ret_%'].max():.1f}%   worst spell: {sp['ret_%'].min():.1f}%")


if __name__ == "__main__":
    main()
