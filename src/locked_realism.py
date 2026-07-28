"""
LIVE-REALISM checks for the LOCKED SPOT strategy (trend_ensemble, threshold 0.5):

  A) DRAWDOWN DURATION / UNDERWATER analysis — not just how deep, but how LONG.
     Every drawdown episode (peak -> trough -> recovery) with dates and day counts,
     the longest time spent underwater, and current underwater status.

  B) NEXT-OPEN FILL realism — the engine assumes you trade at the signal's close.
     Here we re-run filling at the NEXT OPEN instead (decision at close t, fill at
     the following open, marked open-to-open, causal) and compare to baseline. If the
     edge is real it should barely move.

Usage:  python src/locked_realism.py
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


# ---------------------------------------------------------------- A) underwater #
def drawdown_episodes(ret: pd.Series) -> pd.DataFrame:
    eq = (1 + ret).cumprod()
    peak = eq.cummax()
    uw = eq / peak - 1
    idx = eq.index
    rows = []
    in_dd = False
    p_date = idx[0]; p_val = eq.iloc[0]; t_date = idx[0]; t_val = eq.iloc[0]
    for t in range(len(eq)):
        e = eq.iloc[t]
        if not in_dd:
            if e < peak.iloc[t] - 1e-12:            # dropped below prior peak -> DD starts
                in_dd = True
                p_val = peak.iloc[t]
                # peak date = last bar at/above that peak before this one
                p_date = idx[t - 1] if t > 0 else idx[t]
                t_val = e; t_date = idx[t]
        else:
            if e < t_val:                            # new trough
                t_val = e; t_date = idx[t]
            if e >= p_val - 1e-12:                    # recovered to old peak
                rows.append({
                    "peak_date": p_date.date(), "trough_date": t_date.date(),
                    "recovery_date": idx[t].date(),
                    "depth_%": round((t_val / p_val - 1) * 100, 1),
                    "to_trough_d": (t_date - p_date).days,
                    "to_recover_d": (idx[t] - t_date).days,
                    "underwater_d": (idx[t] - p_date).days,
                })
                in_dd = False
    if in_dd:                                        # still underwater at end of data
        rows.append({
            "peak_date": p_date.date(), "trough_date": t_date.date(),
            "recovery_date": "OPEN",
            "depth_%": round((t_val / p_val - 1) * 100, 1),
            "to_trough_d": (t_date - p_date).days,
            "to_recover_d": (idx[-1] - t_date).days,
            "underwater_d": (idx[-1] - p_date).days,
        })
    return pd.DataFrame(rows), uw


# ------------------------------------------------------------- B) next-open fill #
def next_open_returns(df, target, fee, slip):
    """Decision at close[t] -> fill at the following open -> marked open-to-open.
    strat[t] = target[t-1] * (open[t+1]/open[t]-1), costs on executed turnover. Causal."""
    g = (df["open"].shift(-1) / df["open"] - 1).fillna(0.0)     # hold open[t]->open[t+1]
    executed = target.shift(1).reindex(df.index).ffill().fillna(0.0).clip(0.0, 1.0)
    dpos = executed.diff().abs().fillna(executed.abs())
    cost = dpos * (fee + slip)
    return executed * g - cost


def line(tag, ret):
    s = M.summary(ret, C.BARS_PER_YEAR)
    print(f"  {tag:<28} ret={s['total_return']*100:9.1f}%  cagr={s['cagr']*100:6.2f}%  "
          f"sharpe={s['sharpe']:5.2f}  sortino={s['sortino']:5.2f}  maxDD={s['max_drawdown']*100:6.2f}%")


def main():
    full = load()
    sig = trend_ensemble(full, C.THRESHOLD).loc[C.BACKTEST_START:]
    df = full.loc[C.BACKTEST_START:]
    res = run_backtest(df, sig, C.FEE, C.SLIPPAGE, C.BARS_PER_YEAR)
    ret_close = res.returns

    print("\n" + "=" * 92)
    print("A) DRAWDOWN DURATION / UNDERWATER  (LOCKED spot, close-fill baseline)")
    print("=" * 92)
    ep, uw = drawdown_episodes(ret_close)
    ep_sorted = ep.sort_values("depth_%")                       # deepest first (most negative)
    print("\nTop 8 drawdowns by depth:")
    print(ep_sorted.head(8).to_string(index=False))
    longest = ep.sort_values("underwater_d", ascending=False).head(5)
    print("\nTop 5 LONGEST underwater stretches (days peak->recovery):")
    print(longest[["peak_date", "trough_date", "recovery_date",
                   "depth_%", "underwater_d"]].to_string(index=False))
    cur = uw.iloc[-1] * 100
    print(f"\n  episodes: {len(ep)}   deepest: {ep['depth_%'].min():.1f}%   "
          f"longest underwater: {ep['underwater_d'].max()} days "
          f"({ep['underwater_d'].max()/365:.1f} yr)")
    print(f"  median recovery time (trough->new high): {ep['to_recover_d'].median():.0f} days")
    print(f"  % of all days spent underwater: {(uw < -1e-9).mean()*100:.0f}%")
    print(f"  CURRENT status: {cur:+.1f}% from peak" +
          ("  (at/near highs)" if cur > -1 else ""))

    print("\n" + "=" * 92)
    print("B) NEXT-OPEN FILL vs CLOSE FILL  (does the edge survive realistic execution?)")
    print("=" * 92)
    ret_open = next_open_returns(df, sig, C.FEE, C.SLIPPAGE)
    line("close fill (baseline)", ret_close)
    line("next-open fill (realistic)", ret_open)
    dd_c = M.max_drawdown(ret_close) * 100
    dd_o = M.max_drawdown(ret_open) * 100
    print(f"\n  Sharpe change: {M.sharpe(ret_open,C.BARS_PER_YEAR)-M.sharpe(ret_close,C.BARS_PER_YEAR):+.3f}   "
          f"maxDD change: {dd_o-dd_c:+.1f}pp   "
          f"return change: {(M.total_return(ret_open)-M.total_return(ret_close))*100:+.0f}pp")


if __name__ == "__main__":
    main()
