"""Performance metrics computed from a per-bar return series."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _ann_factor(bars_per_year: float) -> float:
    return bars_per_year


def total_return(returns: pd.Series) -> float:
    return float((1 + returns).prod() - 1)


def cagr(returns: pd.Series, bars_per_year: float) -> float:
    n = len(returns)
    if n == 0:
        return 0.0
    growth = (1 + returns).prod()
    years = n / bars_per_year
    if growth <= 0 or years <= 0:
        return -1.0
    return float(growth ** (1 / years) - 1)


def ann_vol(returns: pd.Series, bars_per_year: float) -> float:
    return float(returns.std() * np.sqrt(bars_per_year))


def sharpe(returns: pd.Series, bars_per_year: float, rf: float = 0.0) -> float:
    excess = returns - rf / bars_per_year
    sd = excess.std()
    if sd == 0 or np.isnan(sd):
        return 0.0
    return float(excess.mean() / sd * np.sqrt(bars_per_year))


def sortino(returns: pd.Series, bars_per_year: float, rf: float = 0.0) -> float:
    excess = returns - rf / bars_per_year
    downside = excess[excess < 0]
    dd = downside.std()
    if dd == 0 or np.isnan(dd):
        return 0.0
    return float(excess.mean() / dd * np.sqrt(bars_per_year))


def max_drawdown(returns: pd.Series) -> float:
    equity = (1 + returns).cumprod()
    peak = equity.cummax()
    dd = equity / peak - 1
    return float(dd.min())


def calmar(returns: pd.Series, bars_per_year: float) -> float:
    mdd = max_drawdown(returns)
    if mdd == 0:
        return 0.0
    return float(cagr(returns, bars_per_year) / abs(mdd))


def summary(returns: pd.Series, bars_per_year: float, trades: int = 0,
            exposure: float = 1.0) -> dict:
    return {
        "total_return": total_return(returns),
        "cagr": cagr(returns, bars_per_year),
        "ann_vol": ann_vol(returns, bars_per_year),
        "sharpe": sharpe(returns, bars_per_year),
        "sortino": sortino(returns, bars_per_year),
        "max_drawdown": max_drawdown(returns),
        "calmar": calmar(returns, bars_per_year),
        "exposure": exposure,
        "trades": trades,
    }


def rolling_returns(returns: pd.Series, window_bars: int) -> pd.Series:
    """Rolling total return over a trailing window (e.g. 30-day = 24*30 bars)."""
    logret = np.log1p(returns)
    return np.expm1(logret.rolling(window_bars, min_periods=window_bars).sum())
