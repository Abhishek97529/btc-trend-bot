"""
SWING-trade BTC/USDT perp-futures search. Goal: beat the LOCKED SPOT strategy
(Sharpe 1.18, maxDD -38%) on a risk-adjusted basis — not just buy & hold.

Swing = days-to-weeks holds (vs the locked ensemble's ~months). Structural edge for
leveraged perps: you pay funding only while IN a trade, so short holds + leverage can
beat a long-hold position strategy IF the entries have edge.

Candidate swing setups (all LONG/FLAT, daily bars, causal, act on t+1):
  RSI     pullback-in-uptrend: regime long, buy RSI oversold bounce, exit overbought
  DONCH   short breakout: enter 20d-high breakout in uptrend, exit 10d-low
  BB      Bollinger reversion: buy lower band in uptrend, exit at mid band
  MACD    momentum swing: enter MACD>signal in uptrend, exit MACD<signal
Each has an ATR trailing stop + max-hold cap (that's what makes it a swing, not a
position trade). Leverage: vol-capped clip(tv/realized_vol, 0, 5) -> dynamic 0..5x.

Honest engine reused from futures_5x (funding ~11.7%/yr, intraday liquidation, same
fees). Train pre-2023 / test 2023+ unseen. YoY returns printed for every strategy.

Usage:  python src/swing_futures.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indicators import sma, ema, rsi, atr, donchian, rolling_vol
from strategies_v2 import trend_ensemble
from backtest import run_backtest
from futures_5x import leveraged_returns, count_trades, MAX_LEV
import metrics as M

BPY = 365
DATA = Path(__file__).resolve().parent.parent / "data" / "BTCUSDT_1d_2017-08-01_2026-07-23.parquet"
START, SPLIT = "2018-06-01", "2023-01-01"
TV = 1.0                # vol target for leverage sizing
ATR_STOP = 3.0         # trailing stop = entry/high - ATR_STOP * ATR
MAX_HOLD = 20          # swing cap in days


def load():
    df = pd.read_parquet(DATA)
    return df[~df.index.duplicated()].sort_index().loc[START:]


def bollinger(c, n=20, k=2.0):
    m = sma(c, n); sd = c.rolling(n, min_periods=n).std()
    return m - k * sd, m, m + k * sd


def macd(c, fast=12, slow=26, sig=9):
    line = ema(c, fast) - ema(c, slow)
    signal = line.ewm(span=sig, adjust=False, min_periods=sig).mean()
    return line, signal


def stateful_position(df, entry, exit_, use_stop=True):
    """Build a 0/1 long/flat position from entry/exit signals + ATR trailing stop +
    max-hold cap. Causal: decisions use bar-t info; engine executes on t+1."""
    c = df["close"].values
    a = atr(df, 14).values
    entry = entry.reindex(df.index).fillna(False).values
    exit_ = exit_.reindex(df.index).fillna(False).values
    pos = np.zeros(len(df))
    in_pos = False; bars_held = 0; trail = 0.0
    for t in range(len(df)):
        if in_pos:
            bars_held += 1
            if not np.isnan(a[t]):
                trail = max(trail, c[t] - ATR_STOP * a[t])   # ratchet up
            hit_stop = use_stop and c[t] < trail
            if exit_[t] or bars_held >= MAX_HOLD or hit_stop:
                in_pos = False; bars_held = 0
        else:
            if entry[t]:
                in_pos = True; bars_held = 0
                trail = (c[t] - ATR_STOP * a[t]) if not np.isnan(a[t]) else 0.0
        pos[t] = 1.0 if in_pos else 0.0
    return pd.Series(pos, index=df.index)


def swing_signals(df):
    c = df["close"]
    regime = (c > sma(c, 200))                       # only swing-long in uptrends
    r = rsi(c, 14)
    lower, mid, upper = bollinger(c, 20, 2.0)
    up20, _ = donchian(df, 20); _, dn10 = donchian(df, 10)
    ml, sl = macd(c)

    out = {}
    # RSI pullback: enter when RSI crosses back above 35 in uptrend; exit RSI>65
    out["RSI"] = stateful_position(df,
        entry=regime & (r > 35) & (r.shift(1) <= 35),
        exit_=(r > 65))
    # Donchian short breakout: enter 20d-high break in uptrend; exit 10d-low break
    out["DONCH"] = stateful_position(df,
        entry=regime & (c > up20),
        exit_=(c < dn10))
    # Bollinger reversion: enter below lower band in uptrend; exit at/above mid band
    out["BB"] = stateful_position(df,
        entry=regime & (c < lower),
        exit_=(c > mid))
    # MACD momentum: enter MACD cross-up in uptrend; exit cross-down
    out["MACD"] = stateful_position(df,
        entry=regime & (ml > sl) & (ml.shift(1) <= sl.shift(1)),
        exit_=(ml < sl))
    return out


def vol_leverage(df, pos):
    """Dynamic vol-capped leverage 0..5x, applied while in position, executed t+1."""
    rv = (rolling_vol(df["close"].pct_change(), 30) * np.sqrt(BPY)).shift(1)
    volcap = (TV / rv).clip(0.0, MAX_LEV).fillna(0.0)
    return (pos * volcap).shift(1).fillna(0.0)


def run_futures(df, lev):
    underlying = df["close"].pct_change().fillna(0.0)
    low_ret = (df["low"] / df["close"].shift(1) - 1).fillna(0.0)
    high_ret = (df["high"] / df["close"].shift(1) - 1).fillna(0.0)
    return leveraged_returns(df, lev, underlying, low_ret, high_ret)


def hold_stats(pos):
    """median/mean holding period (days) of the 0/1 position, and #swings."""
    p = pos.values; holds = []; cur = 0
    for x in p:
        if x > 0: cur += 1
        elif cur > 0: holds.append(cur); cur = 0
    if cur > 0: holds.append(cur)
    if not holds: return 0, 0, 0
    return int(np.median(holds)), round(float(np.mean(holds)), 1), len(holds)


def metric_row(ret, liq=None, pos=None):
    s = M.summary(ret, BPY)
    row = {"ret%": s["total_return"]*100, "cagr%": s["cagr"]*100, "sharpe": s["sharpe"],
           "sortino": s["sortino"], "maxDD%": s["max_drawdown"]*100, "calmar": s["calmar"],
           "liq": "YES" if liq is not None else ""}
    if pos is not None:
        med, mean, nsw = hold_stats(pos)
        row["medHold"] = med; row["swings"] = nsw
    return row


def yoy(ret, name):
    return ret.groupby(ret.index.year).apply(lambda r: (1+r).prod()-1) * 100


def build_all(df):
    """Return {name: (net_returns, liq, position_or_None)} for all benchmarks+swings."""
    res = {}
    # benchmarks
    res["BUYHOLD"] = (df["close"].pct_change().fillna(0.0), None, None)
    locked = run_backtest(df, trend_ensemble(df, 0.5), 0.001, 0.0005, BPY)
    res["LOCKED_SPOT"] = (locked.returns, None, None)
    # swing futures
    for name, pos in swing_signals(df).items():
        lev = vol_leverage(df, pos)
        ret, liq = run_futures(df, lev)
        res[f"SWING_{name}"] = (ret, liq, pos.shift(1).fillna(0.0))
    return res


def window(df, res, lo, hi, tag):
    sl = df.loc[lo:hi] if hi else df.loc[lo:]
    idx = sl.index
    rows = {}
    for name, (ret, liq, pos) in res.items():
        r = ret.reindex(idx).fillna(0.0)
        p = pos.reindex(idx) if pos is not None else None
        rows[name] = metric_row(r, liq if (hi is None) else None, p)
    print(f"\n{'='*112}\n{tag}  ({idx[0].date()} -> {idx[-1].date()}, {len(idx)} bars)\n{'='*112}")
    print(pd.DataFrame(rows).T.round(2).to_string())


def main():
    df = load()
    res = build_all(df)
    window(df, res, START, SPLIT, "TRAIN (pre-2023, in-sample)")
    window(df, res, SPLIT, None, "TEST (2023+, UNSEEN)")
    window(df, res, START, None, "FULL CYCLE")

    print(f"\n{'='*112}\nYEAR-OVER-YEAR RETURNS (%)  — every strategy, full record\n{'='*112}")
    yoy_tbl = pd.DataFrame({name: yoy(ret, name) for name, (ret, _, _) in res.items()})
    print(yoy_tbl.round(1).to_string())


if __name__ == "__main__":
    main()
