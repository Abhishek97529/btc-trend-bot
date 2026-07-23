"""
Candidate strategies.

Each strategy is a function (df, **params) -> pd.Series of TARGET POSITIONS in [0, 1]
aligned to df.index, where the value at bar t is the position we WANT to hold based on
information available at the close of bar t. The backtest engine shifts this forward by
one bar before applying it, so we always trade on the *next* bar's open — no lookahead.

We trade spot BTC, so positions are long-only: 1.0 = fully in BTC, 0.0 = in cash.

Each strategy exposes a `.param_grid` for the optimizer to search.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indicators import ema, rsi, atr, donchian, sma


# --------------------------------------------------------------------------- #
# Benchmark
# --------------------------------------------------------------------------- #
def buy_and_hold(df: pd.DataFrame) -> pd.Series:
    return pd.Series(1.0, index=df.index)


# --------------------------------------------------------------------------- #
# 1. EMA trend-following crossover
# --------------------------------------------------------------------------- #
def ema_crossover(df: pd.DataFrame, fast: int = 24, slow: int = 96) -> pd.Series:
    f, s = ema(df["close"], fast), ema(df["close"], slow)
    pos = (f > s).astype(float)
    return pos.where(f.notna() & s.notna(), 0.0)


ema_crossover.param_grid = {
    "fast": [12, 24, 48, 72],
    "slow": [96, 168, 240, 336],
}


# --------------------------------------------------------------------------- #
# 2. Donchian channel breakout (classic turtle-style trend)
# --------------------------------------------------------------------------- #
def donchian_breakout(df: pd.DataFrame, entry: int = 96, exit_n: int = 48) -> pd.Series:
    up, _ = donchian(df, entry)
    _, dn_exit = donchian(df, exit_n)
    close = df["close"]
    pos = pd.Series(np.nan, index=df.index)
    pos[close > up] = 1.0        # breakout up -> enter long
    pos[close < dn_exit] = 0.0   # break below exit channel -> flat
    return pos.ffill().fillna(0.0)


donchian_breakout.param_grid = {
    "entry": [48, 96, 168, 336],
    "exit_n": [24, 48, 96],
}


# --------------------------------------------------------------------------- #
# 3. RSI mean reversion (buy dips, exit on recovery) with trend filter
# --------------------------------------------------------------------------- #
def rsi_mean_reversion(
    df: pd.DataFrame, n: int = 14, lower: int = 30, upper: int = 55, trend: int = 200
) -> pd.Series:
    r = rsi(df["close"], n)
    trend_ma = sma(df["close"], trend)
    uptrend = df["close"] > trend_ma  # only buy dips inside an uptrend
    pos = pd.Series(np.nan, index=df.index)
    pos[(r < lower) & uptrend] = 1.0
    pos[r > upper] = 0.0
    return pos.ffill().fillna(0.0)


rsi_mean_reversion.param_grid = {
    "n": [7, 14, 21],
    "lower": [25, 30, 35],
    "upper": [50, 55, 65],
    "trend": [100, 200, 300],
}


# --------------------------------------------------------------------------- #
# 4. Time-series momentum with a volatility/trend regime filter
# --------------------------------------------------------------------------- #
def momentum(df: pd.DataFrame, lookback: int = 168, trend: int = 200) -> pd.Series:
    roc = df["close"].pct_change(lookback)
    trend_ma = sma(df["close"], trend)
    pos = ((roc > 0) & (df["close"] > trend_ma)).astype(float)
    return pos.where(roc.notna() & trend_ma.notna(), 0.0)


momentum.param_grid = {
    "lookback": [48, 96, 168, 336],
    "trend": [100, 200, 400],
}


# --------------------------------------------------------------------------- #
# 5. Trend + ATR volatility-targeted position (scales exposure by regime)
# --------------------------------------------------------------------------- #
def trend_vol_target(
    df: pd.DataFrame, trend: int = 200, atr_n: int = 48, target_atr_pct: float = 0.02
) -> pd.Series:
    """Long only when above trend MA; size inversely to volatility (capped at 1.0)."""
    trend_ma = sma(df["close"], trend)
    a = atr(df, atr_n) / df["close"]
    size = (target_atr_pct / a).clip(0.0, 1.0)
    long = (df["close"] > trend_ma).astype(float)
    pos = (long * size)
    return pos.where(trend_ma.notna() & a.notna(), 0.0)


trend_vol_target.param_grid = {
    "trend": [100, 200, 300],
    "atr_n": [24, 48, 96],
    "target_atr_pct": [0.015, 0.02, 0.03],
}


# Registry the runner iterates over.
STRATEGIES = {
    "ema_crossover": ema_crossover,
    "donchian_breakout": donchian_breakout,
    "rsi_mean_reversion": rsi_mean_reversion,
    "momentum": momentum,
    "trend_vol_target": trend_vol_target,
}
