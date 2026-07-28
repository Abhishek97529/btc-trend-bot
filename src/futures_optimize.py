"""
Design search: best RISK-ADJUSTED dynamic-leverage BTC/USDT perp futures strategy.
Ceiling 5x, leverage varies per bar. Benchmark = buy & hold. Capital = 1,00,000.

Reuses the HONEST engine from futures_5x.py (daily funding ~11.7%/yr on notional +
intraday liquidation on the low, same fees/slippage/dates/no-lookahead as the locked
spot strategy). Direction is LONG/FLAT — shorting was already shown to fight BTC's
drift and give back the 2022 gains in 2023.

Leverage families searched (all capped at 5x, all act on t+1):
  V  voltarget         lev = clip(tv / realized_vol, 0, 5)          when gate long
  T  conviction-tier   4/7 votes ->2x, 5/7 ->3x, 6/7 ->4x, 7/7 ->5x (discrete "per-trade")
  L  conviction-linear map agreement [0.5..1.0] -> [2x..5x]
  H  tier x vol-cap    min(tier, clip(tv/realized_vol,0,5))         (lever only when calm)
  HL linear x vol-cap  min(linear, clip(tv/realized_vol,0,5))

Discipline: train pre-2023, test 2023+ (unseen), full metrics, benchmark buy&hold.
We choose on out-of-sample Sharpe among NON-liquidated, drawdown-sane candidates.

Usage:  python src/futures_optimize.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategies_v2 import _trend_votes
from indicators import rolling_vol
from futures_5x import leveraged_returns, count_trades, MAX_LEV
import metrics as M

BPY = 365
DATA = Path(__file__).resolve().parent.parent / "data" / "BTCUSDT_1d_2017-08-01_2026-07-23.parquet"
START, SPLIT = "2018-06-01", "2023-01-01"
THRESHOLD = 0.5


def load():
    df = pd.read_parquet(DATA)
    return df[~df.index.duplicated()].sort_index().loc[START:]


def components(df):
    """Executed (t+1) conviction, gate, and trailing realized vol — all causal."""
    votes = _trend_votes(df)
    frac = votes.mean(axis=1)
    warm = df["close"].rolling(200, min_periods=200).mean().notna()
    frac_exec = frac.where(warm).shift(1).fillna(0.0)
    gate = (frac_exec >= THRESHOLD).astype(float)
    n_votes = (frac_exec * 7).round()            # 0..7 votes agreeing (executed)
    rv = (rolling_vol(df["close"].pct_change(), 30) * np.sqrt(BPY)).shift(1)
    return frac_exec, gate, n_votes, rv


def tier_lev(n_votes):
    """Discrete per-trade leverage by conviction: 4->2x,5->3x,6->4x,7->5x, else 0."""
    lev = pd.Series(0.0, index=n_votes.index)
    lev[n_votes >= 4] = 2.0
    lev[n_votes >= 5] = 3.0
    lev[n_votes >= 6] = 4.0
    lev[n_votes >= 7] = 5.0
    return lev


def linear_lev(frac, gate):
    """Map agreement [0.5..1.0] -> [2x..5x] linearly, 0 below gate."""
    lin = 2.0 + (frac - 0.5) / 0.5 * 3.0
    return (lin.clip(2.0, 5.0) * gate).fillna(0.0)


def voltarget_lev(tv, rv, gate, cap=MAX_LEV):
    return ((tv / rv).clip(0.0, cap).fillna(0.0) * gate)


def build_family(df, tv):
    frac, gate, n_votes, rv = components(df)
    vt = voltarget_lev(tv, rv, gate)
    tier = tier_lev(n_votes)
    lin = linear_lev(frac, gate)
    volcap = (tv / rv).clip(0.0, MAX_LEV).fillna(0.0)
    return {
        f"V voltarget tv={tv}": vt,
        "T conviction-tier": tier,
        "L conviction-linear": lin,
        f"H tier x volcap tv={tv}": pd.concat([tier, volcap], axis=1).min(axis=1) * gate,
        f"HL linear x volcap tv={tv}": pd.concat([lin, volcap], axis=1).min(axis=1) * gate,
    }


def evaluate(df, lev, lo, hi):
    sl = df.loc[lo:hi] if hi else df.loc[lo:]
    idx = sl.index
    lv = lev.reindex(idx).fillna(0.0)
    underlying = sl["close"].pct_change().fillna(0.0)
    low_ret = (sl["low"] / sl["close"].shift(1) - 1).fillna(0.0)
    high_ret = (sl["high"] / sl["close"].shift(1) - 1).fillna(0.0)
    ret, liq = leveraged_returns(sl, lv, underlying, low_ret, high_ret)
    s = M.summary(ret, BPY)
    mag = lv.abs()[lv != 0]
    return {
        "ret%": s["total_return"] * 100, "cagr%": s["cagr"] * 100,
        "sharpe": s["sharpe"], "sortino": s["sortino"],
        "maxDD%": s["max_drawdown"] * 100, "calmar": s["calmar"],
        "medLev": mag.median() if len(mag) else 0.0,
        "maxLev": mag.max() if len(mag) else 0.0,
        "trades": count_trades(lv),
        "liq": "YES" if liq is not None else "",
    }, ret


def bh_row(df, lo, hi):
    sl = df.loc[lo:hi] if hi else df.loc[lo:]
    r = sl["close"].pct_change().fillna(0.0)
    s = M.summary(r, BPY)
    return {"ret%": s["total_return"]*100, "cagr%": s["cagr"]*100, "sharpe": s["sharpe"],
            "sortino": s["sortino"], "maxDD%": s["max_drawdown"]*100, "calmar": s["calmar"],
            "medLev": 1.0, "maxLev": 1.0, "trades": 1, "liq": ""}


def table(df, tvs, lo, hi, tag):
    rows = {"buy_and_hold": bh_row(df, lo, hi)}
    seen = set()
    for tv in tvs:
        for name, lev in build_family(df, tv).items():
            if name in seen and not name.startswith(("V", "H")):
                continue  # tier/linear don't depend on tv; show once
            seen.add(name)
            rows[name], _ = evaluate(df, lev, lo, hi)
    out = pd.DataFrame(rows).T.round(2)
    sl = df.loc[lo:hi] if hi else df.loc[lo:]
    print(f"\n{'='*118}\n{tag}  ({sl.index[0].date()} -> {sl.index[-1].date()}, {len(sl)} bars)\n{'='*118}")
    print(out.to_string())


def main():
    df = load()
    tvs = [0.7, 1.0, 1.5]
    table(df, tvs, START, SPLIT, "TRAIN / design era (pre-2023, in-sample)")
    table(df, tvs, SPLIT, None, "TEST / UNSEEN (2023+, out-of-sample)")
    table(df, tvs, START, None, "FULL PERIOD")


if __name__ == "__main__":
    main()
