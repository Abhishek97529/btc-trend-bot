"""
Refinement: BALANCED dynamic-leverage BTC/USDT perp futures (tier x vol-cap, tv=1.0,
median ~1.9x) + a DRAWDOWN CIRCUIT-BREAKER, tuned to MAX Sharpe while capping the
worst-case drawdown. Benchmark = buy & hold. Capital = 1,00,000.

Base leverage (per bar, act on t+1, capped 5x):
    lev = min( conviction_tier(4->2x,5->3x,6->4x,7->5x),  clip(tv/realized_vol,0,5) ) * gate
i.e. lever up only when conviction is high AND the market is calm.

Circuit-breaker (the new piece — causal, uses equity through t-1 only):
    dd = equity_{t-1} / running_peak_{t-1} - 1
    brake = 1                       if dd > -TRIGGER
          = linear ramp 1 -> 0      as dd goes -TRIGGER -> -FLOOR
          = 0 (fully de-risked)     if dd <= -FLOOR
    effective_lev_t = base_lev_t * brake_t
De-risking as the account bleeds is what caps the drawdown; risk comes back
automatically as equity recovers toward its peak. No lookahead.

Honest engine reused from futures_5x: daily funding ~11.7%/yr on notional, intraday
liquidation on the low, same fees/slippage/dates. Train pre-2023, test 2023+ unseen.

Usage:  python src/futures_refine.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategies_v2 import _trend_votes
from indicators import rolling_vol
from futures_5x import FEE, SLIP, FUNDING_DAILY, MAINT, MAX_LEV
import metrics as M

BPY = 365
DATA = Path(__file__).resolve().parent.parent / "data" / "BTCUSDT_1d_2017-08-01_2026-07-23.parquet"
START, SPLIT = "2018-06-01", "2023-01-01"
THRESHOLD, TV = 0.5, 1.0


def load():
    df = pd.read_parquet(DATA)
    return df[~df.index.duplicated()].sort_index().loc[START:]


def base_leverage(df):
    """Balanced tier x vol-cap base leverage, executed (t+1), long/flat."""
    votes = _trend_votes(df)
    frac = votes.mean(axis=1)
    warm = df["close"].rolling(200, min_periods=200).mean().notna()
    frac_exec = frac.where(warm).shift(1).fillna(0.0)
    gate = (frac_exec >= THRESHOLD).astype(float)
    n = (frac_exec * 7).round()
    tier = pd.Series(0.0, index=df.index)
    tier[n >= 4] = 2.0; tier[n >= 5] = 3.0; tier[n >= 6] = 4.0; tier[n >= 7] = 5.0
    rv = (rolling_vol(df["close"].pct_change(), 30) * np.sqrt(BPY)).shift(1)
    volcap = (TV / rv).clip(0.0, MAX_LEV).fillna(0.0)
    return pd.concat([tier, volcap], axis=1).min(axis=1) * gate


def run_with_brake(df, base_lev, trigger=None, floor=None):
    """Stateful sim: apply the drawdown brake to base leverage, funding + liquidation.
    trigger/floor = None -> no brake (raw base). Returns (net returns series, liq idx)."""
    idx = df.index
    lev0 = base_lev.reindex(idx).fillna(0.0).values
    underlying = df["close"].pct_change().fillna(0.0).values
    low_ret = (df["low"] / df["close"].shift(1) - 1).fillna(0.0).values
    high_ret = (df["high"] / df["close"].shift(1) - 1).fillna(0.0).values

    rets = np.zeros(len(idx))
    equity, peak, prev_notional, liq = 1.0, 1.0, 0.0, None
    for t in range(len(idx)):
        dd = equity / peak - 1.0                       # causal: state through t-1
        if trigger is None:
            brake = 1.0
        elif dd > -trigger:
            brake = 1.0
        elif dd <= -floor:
            brake = 0.0
        else:
            brake = (dd + floor) / (floor - trigger)   # ramp 1 -> 0
        notional = lev0[t] * brake
        adverse = low_ret[t] if notional > 0 else high_ret[t]
        if notional != 0 and notional * adverse <= -(1 - MAINT):
            rets[t] = -1.0; liq = t
            equity = 0.0
            break
        pnl = notional * underlying[t]
        funding = FUNDING_DAILY * abs(notional)
        turn = abs(notional - prev_notional) * (FEE + SLIP)
        prev_notional = notional
        r = pnl - funding - turn
        rets[t] = r
        equity *= (1 + r)
        peak = max(peak, equity)
    return pd.Series(rets, index=idx), liq


def stat(df, ret, liq=None):
    s = M.summary(ret, BPY)
    return {"ret%": s["total_return"]*100, "cagr%": s["cagr"]*100, "sharpe": s["sharpe"],
            "sortino": s["sortino"], "maxDD%": s["max_drawdown"]*100, "calmar": s["calmar"],
            "liq": "YES" if liq is not None else ""}


def evalw(df, base_lev, lo, hi, trigger, floor):
    sl = df.loc[lo:hi] if hi else df.loc[lo:]
    ret, liq = run_with_brake(sl, base_lev, trigger, floor)
    return stat(sl, ret, liq), ret


def main():
    df = load()
    base = base_leverage(df)

    # ---- sweep breaker params on TRAIN, maximize Sharpe with maxDD shallower than 50% #
    grid = [(None, None)] + [(tr, fl) for tr in (0.10, 0.15, 0.20, 0.25)
                             for fl in (0.30, 0.35, 0.40, 0.45) if fl > tr]
    print(f"\n{'='*104}\nBREAKER SWEEP on TRAIN (pre-2023) — pick max Sharpe with maxDD > -50%\n{'='*104}")
    print(f"{'trigger/floor':<16}{'ret%':>10}{'cagr%':>8}{'sharpe':>8}{'maxDD%':>9}{'calmar':>8}")
    best = None
    for tr, fl in grid:
        s, _ = evalw(df, base, START, SPLIT, tr, fl)
        tag = "none (raw)" if tr is None else f"{int(tr*100)}/{int(fl*100)}"
        print(f"{tag:<16}{s['ret%']:>10.0f}{s['cagr%']:>8.1f}{s['sharpe']:>8.2f}{s['maxDD%']:>9.1f}{s['calmar']:>8.2f}")
        if tr is not None and s["maxDD%"] > -50 and (best is None or s["sharpe"] > best[0]):
            best = (s["sharpe"], tr, fl)
    _, btr, bfl = best
    print(f"\n>> chosen breaker: trigger -{int(btr*100)}%  floor -{int(bfl*100)}%  (best TRAIN Sharpe, DD-capped)")

    # ---- trigger-robustness: compare whippy vs wide triggers across ALL windows -- #
    triggers = [(None, None), (0.10, 0.45), (0.15, 0.45), (0.20, 0.45)]
    for tag, lo, hi in [("TRAIN (pre-2023, in-sample)", START, SPLIT),
                        ("TEST (2023+, UNSEEN)", SPLIT, None),
                        ("FULL CYCLE", START, None)]:
        sl = df.loc[lo:hi] if hi else df.loc[lo:]
        bh = M.summary(sl["close"].pct_change().fillna(0.0), BPY)
        rows = {"buy_and_hold": {"ret%": bh['total_return']*100, "cagr%": bh['cagr']*100,
                                 "sharpe": bh['sharpe'], "sortino": bh['sortino'],
                                 "maxDD%": bh['max_drawdown']*100, "calmar": bh['calmar'], "liq": ""}}
        for tr, fl in triggers:
            s, _ = evalw(df, base, lo, hi, tr, fl)
            name = "base (no brake)" if tr is None else f"brake {int(tr*100)}/{int(fl*100)}"
            rows[name] = s
        print(f"\n{'='*104}\n{tag}  ({sl.index[0].date()} -> {sl.index[-1].date()})\n{'='*104}")
        print(pd.DataFrame(rows).T.round(2).to_string())


if __name__ == "__main__":
    main()
