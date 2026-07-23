"""
Round-2 strategies, designed to have a real shot at beating buy-and-hold on a
risk-adjusted basis -- and to resist overfitting.

Key ideas:
  * ENSEMBLES over single signals: vote across many FIXED-length trend signals and
    size exposure by how many agree. Few/no free params => far less curve-fitting.
  * VOLATILITY TARGETING: scale exposure so the portfolio targets a constant vol;
    cuts exposure in manic regimes, adds it in calm uptrends.
  * REGIME FILTER: the 200-period MA as a risk-on/off switch.

All are long/flat (spot-deployable). `trend_ls` is long/short for futures research only.
Positions are in [0, 1] (or [-1, 1] for the LS variant). No lookahead: value at bar t
uses only info through t; the engine trades it on t+1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indicators import ema, sma, atr, donchian, rolling_vol


# --------------------------------------------------------------------------- #
# Building block: a basket of fixed trend signals -> agreement fraction [0,1]
# --------------------------------------------------------------------------- #
def _trend_votes(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    votes = pd.DataFrame(index=df.index)
    votes["above_sma50"] = (c > sma(c, 50)).astype(float)
    votes["above_sma100"] = (c > sma(c, 100)).astype(float)
    votes["above_sma200"] = (c > sma(c, 200)).astype(float)
    votes["ema20_50"] = (ema(c, 20) > ema(c, 50)).astype(float)
    votes["ema50_100"] = (ema(c, 50) > ema(c, 100)).astype(float)
    up, dn = donchian(df, 55)
    dstate = pd.Series(np.nan, index=df.index)
    dstate[c > up] = 1.0
    dstate[c < dn] = 0.0
    votes["donchian55"] = dstate.ffill().fillna(0.0)
    votes["mom90"] = (c.pct_change(90) > 0).astype(float)
    return votes


def trend_ensemble(df: pd.DataFrame, threshold: float = 0.5) -> pd.Series:
    """Exposure = fraction of trend signals in agreement, gated by a threshold.
    threshold is the ONLY knob (and it's coarse)."""
    votes = _trend_votes(df)
    frac = votes.mean(axis=1)
    pos = frac.where(frac >= threshold, 0.0)
    warmup = df["close"].rolling(200, min_periods=200).mean().notna()
    return (pos * warmup).fillna(0.0)


trend_ensemble.param_grid = {"threshold": [0.4, 0.5, 0.6, 0.7]}


# --------------------------------------------------------------------------- #
# Volatility-targeted trend: ensemble direction, sized to a target annual vol
# --------------------------------------------------------------------------- #
def vol_target_trend(
    df: pd.DataFrame, target_vol: float = 0.60, vol_win: int = 30, bars_per_year: int = 365
) -> pd.Series:
    votes = _trend_votes(df)
    direction = (votes.mean(axis=1) >= 0.5).astype(float)  # risk-on gate
    ret = df["close"].pct_change()
    realized = rolling_vol(ret, vol_win) * np.sqrt(bars_per_year)
    scale = (target_vol / realized).clip(0.0, 1.0)          # long-only, no leverage
    pos = direction * scale
    warmup = df["close"].rolling(200, min_periods=200).mean().notna()
    return (pos * warmup).fillna(0.0)


vol_target_trend.param_grid = {
    "target_vol": [0.4, 0.5, 0.6, 0.8],
    "vol_win": [20, 30, 60],
}


# --------------------------------------------------------------------------- #
# Classic 200-MA regime filter (risk-on / risk-off), single knob
# --------------------------------------------------------------------------- #
def ma_regime(df: pd.DataFrame, ma: int = 200, buffer: float = 0.0) -> pd.Series:
    c = df["close"]
    line = sma(c, ma)
    pos = (c > line * (1 + buffer)).astype(float)
    return pos.where(line.notna(), 0.0)


ma_regime.param_grid = {"ma": [100, 150, 200, 250], "buffer": [0.0, 0.02, 0.05]}


# --------------------------------------------------------------------------- #
# Vol-targeted trend, LONG/SHORT (FUTURES ONLY -- not spot-deployable)
# Includes a crude funding/borrow cost handled in the runner via turnover.
# --------------------------------------------------------------------------- #
def trend_ls(df: pd.DataFrame, target_vol: float = 0.6, vol_win: int = 30,
             bars_per_year: int = 365) -> pd.Series:
    votes = _trend_votes(df)
    frac = votes.mean(axis=1)
    direction = np.where(frac >= 0.5, 1.0, -1.0)
    direction = pd.Series(direction, index=df.index)
    ret = df["close"].pct_change()
    realized = rolling_vol(ret, vol_win) * np.sqrt(bars_per_year)
    scale = (target_vol / realized).clip(0.0, 1.0)
    pos = direction * scale
    warmup = df["close"].rolling(200, min_periods=200).mean().notna()
    return (pos * warmup).fillna(0.0)


trend_ls.param_grid = {"target_vol": [0.4, 0.6, 0.8], "vol_win": [20, 30, 60]}


STRATEGIES_LONG = {
    "trend_ensemble": trend_ensemble,
    "vol_target_trend": vol_target_trend,
    "ma_regime": ma_regime,
}
STRATEGIES_LS = {"trend_ls_futures": trend_ls}
