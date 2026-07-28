"""
INTRADAY multi-timeframe SWING search on BTC/USDT perp futures.
Goal: beat the LOCKED SPOT strategy (annualized Sharpe 1.18, maxDD -38%).

Idea: DAILY trend as the regime gate (direction) + 4h bars for entry TIMING (swing),
holding days. Dynamic vol-capped leverage up to 5x. Honest costs + funding + intraday
liquidation, scaled to the bar timeframe. Train pre-2023 / test 2023+. YoY always.

Candidates (all long/flat, causal, execute t+1):
  RSI    reversion: daily-uptrend & 4h RSI bounce off oversold; exit overbought
  BB     reversion: daily-uptrend & 4h close below lower band; exit at mid band
  DONCH  breakout : daily-uptrend & 4h N-bar high break; exit M-bar low
  MACD   momentum : daily-uptrend & 4h MACD cross up; exit cross down
Each with an ATR trailing stop + max-hold cap (that's what keeps it a swing).

Usage:  python src/swing_intraday.py [1h|4h]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indicators import sma, ema, rsi, atr, donchian, rolling_vol
from strategies_v2 import trend_ensemble
import metrics as M

DATA = Path(__file__).resolve().parent.parent / "data"
H1 = DATA / "BTCUSDT_1h_2019-01-01_2026-07-23.parquet"
DAILY = DATA / "BTCUSDT_1d_2017-08-01_2026-07-23.parquet"
START, SPLIT = "2019-01-01", "2023-01-01"

FUNDING_DAILY = 0.117 / 365       # ~11.7%/yr on notional
FEE, SLIP = 0.001, 0.0005
MAINT, MAX_LEV = 0.005, 5.0
TV = 1.0
ATR_STOP = 3.0
MAX_HOLD_DAYS = 10


def resample(df, rule):
    o = df["open"].resample(rule).first()
    h = df["high"].resample(rule).max()
    l = df["low"].resample(rule).min()
    c = df["close"].resample(rule).last()
    v = df["volume"].resample(rule).sum()
    out = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}).dropna()
    return out


def daily_regime(intraday_index):
    """Locked daily trend gate (executed = prior day), mapped onto intraday bars."""
    d = pd.read_parquet(DAILY)
    d = d[~d.index.duplicated()].sort_index()
    gate = (trend_ensemble(d, 0.5) > 0).astype(float).shift(1)   # act next day, causal
    gate_by_date = gate.copy(); gate_by_date.index = gate_by_date.index.normalize()
    dser = pd.Series(intraday_index.normalize(), index=intraday_index)
    return dser.map(gate_by_date).fillna(0.0)


def bollinger(c, n=20, k=2.0):
    m = sma(c, n); sd = c.rolling(n, min_periods=n).std()
    return m - k * sd, m


def macd(c, fast=12, slow=26, sig=9):
    line = ema(c, fast) - ema(c, slow)
    return line, line.ewm(span=sig, adjust=False, min_periods=sig).mean()


def stateful(df, entry, exit_, max_hold_bars):
    c = df["close"].values
    a = atr(df, 14).values
    entry = entry.reindex(df.index).fillna(False).values
    exit_ = exit_.reindex(df.index).fillna(False).values
    pos = np.zeros(len(df)); inpos = False; held = 0; trail = 0.0
    for t in range(len(df)):
        if inpos:
            held += 1
            if not np.isnan(a[t]):
                trail = max(trail, c[t] - ATR_STOP * a[t])
            if exit_[t] or held >= max_hold_bars or (c[t] < trail):
                inpos = False; held = 0
        elif entry[t]:
            inpos = True; held = 0
            trail = (c[t] - ATR_STOP * a[t]) if not np.isnan(a[t]) else 0.0
        pos[t] = 1.0 if inpos else 0.0
    return pd.Series(pos, index=df.index)


def signals(df, regime, bpd):
    c = df["close"]
    reg = regime.reindex(df.index).fillna(0.0) > 0
    r = rsi(c, 14)
    lower, mid = bollinger(c, 20, 2.0)
    up, _ = donchian(df, 5 * bpd); _, dn = donchian(df, 2 * bpd)
    ml, sl = macd(c)
    mh = MAX_HOLD_DAYS * bpd
    return {
        "RSI":   stateful(df, reg & (r > 30) & (r.shift(1) <= 30), (r > 70), mh),
        "BB":    stateful(df, reg & (c < lower), (c > mid), mh),
        "DONCH": stateful(df, reg & (c > up), (c < dn), mh),
        "MACD":  stateful(df, reg & (ml > sl) & (ml.shift(1) <= sl.shift(1)), (ml < sl), mh),
    }


def lever(df, pos, bpy):
    rv = (rolling_vol(df["close"].pct_change(), 30) * np.sqrt(bpy)).shift(1)
    volcap = (TV / rv).clip(0.0, MAX_LEV).fillna(0.0)
    return (pos * volcap).shift(1).fillna(0.0)


def engine(df, lv, bpd):
    """TF-aware leveraged perp: per-bar funding, intraday liquidation, turnover cost."""
    c = df["close"].values
    lv = lv.reindex(df.index).fillna(0.0).values
    under = df["close"].pct_change().fillna(0.0).values
    low_ret = (df["low"] / df["close"].shift(1) - 1).fillna(0.0).values
    fund_bar = FUNDING_DAILY / bpd
    rets = np.zeros(len(df)); prev = 0.0; liq = None
    for t in range(len(df)):
        n = lv[t]
        if n != 0 and n * low_ret[t] <= -(1 - MAINT):
            rets[t] = -1.0; liq = t; break
        rets[t] = n * under[t] - fund_bar * abs(n) - abs(n - prev) * (FEE + SLIP)
        prev = n
    return pd.Series(rets, index=df.index), liq


def holds_days(pos, bpd):
    p = pos.values; hs = []; cur = 0
    for x in p:
        if x > 0: cur += 1
        elif cur > 0: hs.append(cur); cur = 0
    if cur > 0: hs.append(cur)
    return (round(np.median(hs)/bpd, 1), len(hs)) if hs else (0, 0)


def row(ret, bpy, liq=None, pos=None, bpd=1):
    s = M.summary(ret, bpy)
    d = {"ret%": s["total_return"]*100, "cagr%": s["cagr"]*100, "sharpe": s["sharpe"],
         "sortino": s["sortino"], "maxDD%": s["max_drawdown"]*100, "calmar": s["calmar"],
         "liq": "YES" if liq is not None else ""}
    if pos is not None:
        d["holdD"], d["swings"] = holds_days(pos, bpd)
    return d


def window(rows_src, lo, hi, tag, bpy, bpd):
    print(f"\n{'='*108}\n{tag}\n{'='*108}")
    out = {}
    for name, (ret, liq, pos) in rows_src.items():
        r = ret.loc[lo:hi] if hi else ret.loc[lo:]
        p = (pos.loc[lo:hi] if hi else pos.loc[lo:]) if pos is not None else None
        out[name] = row(r, bpy, liq if hi is None else None, p, bpd)
    print(pd.DataFrame(out).T.round(2).to_string())


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "4h"
    bpd = {"1h": 24, "4h": 6}[tf]
    bpy = bpd * 365
    df1 = pd.read_parquet(H1); df1 = df1[~df1.index.duplicated()].sort_index()
    df = resample(df1, {"1h": "1h", "4h": "4h"}[tf]).loc[START:]
    reg = daily_regime(df.index)
    print(f"\nTimeframe {tf}  ({df.index[0]} -> {df.index[-1]}, {len(df)} bars, bpy={bpy})")
    print("Benchmark to beat: LOCKED SPOT annualized Sharpe 1.18, maxDD -38% (daily).")

    src = {"BUYHOLD": (df["close"].pct_change().fillna(0.0), None, None)}
    for name, pos in signals(df, reg, bpd).items():
        ret, liq = engine(df, lever(df, pos, bpy), bpd)
        src[f"SWING_{name}"] = (ret, liq, pos.shift(1).fillna(0.0))

    window(src, START, SPLIT, f"TRAIN pre-2023 (in-sample) [{tf}]", bpy, bpd)
    window(src, SPLIT, None, f"TEST 2023+ (UNSEEN) [{tf}]", bpy, bpd)
    window(src, START, None, f"FULL CYCLE [{tf}]", bpy, bpd)

    print(f"\n{'='*108}\nYEAR-OVER-YEAR RETURNS (%) [{tf}]\n{'='*108}")
    yoy = pd.DataFrame({name: ret.groupby(ret.index.year).apply(lambda r: (1+r).prod()-1)*100
                        for name, (ret, _, _) in src.items()})
    print(yoy.round(1).to_string())


if __name__ == "__main__":
    main()
