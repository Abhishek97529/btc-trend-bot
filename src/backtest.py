"""
Vectorized event-aware backtest engine.

Execution model (no lookahead):
  * A strategy produces a TARGET position from information at the close of bar t.
  * We can only act on that at bar t+1, so the executed position is target.shift(1).
  * PnL for bar t+1 is executed_position * (close-to-close return of bar t+1).
  * Every change in position incurs cost = (fee + slippage) * |Δposition|,
    charged on the bar the trade happens.

This is deliberately conservative: real fills on the next open are usually close to
this, and charging costs on turnover penalizes churny strategies the way live trading does.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class BacktestResult:
    equity: pd.Series          # strategy equity curve (starts at 1.0)
    returns: pd.Series         # per-bar strategy returns (net of costs)
    position: pd.Series        # executed position each bar
    trades: int                # number of position changes
    turnover: float            # sum of |Δposition|
    bars_per_year: float

    @property
    def gross_exposure_time(self) -> float:
        return float((self.position.abs() > 1e-9).mean())


def run_backtest(
    df: pd.DataFrame,
    target_position: pd.Series,
    fee: float = 0.001,        # 0.10% Binance spot taker fee, per side
    slippage: float = 0.0005,  # 5 bps assumed slippage, per side
    bars_per_year: float = 24 * 365,
    allow_short: bool = False,     # True -> futures-style, positions in [-1, 1]
    holding_cost: float = 0.0,     # per-bar cost on |position| (e.g. funding/borrow)
) -> BacktestResult:
    close = df["close"]
    bar_ret = close.pct_change().fillna(0.0)

    lo = -1.0 if allow_short else 0.0
    # Execute on the next bar -> shift target forward by one.
    pos = target_position.reindex(df.index).ffill().fillna(0.0).clip(lo, 1.0)
    executed = pos.shift(1).fillna(0.0)

    # Turnover & costs charged when the executed position actually changes.
    dpos = executed.diff().abs().fillna(executed.abs())
    cost = dpos * (fee + slippage)

    strat_ret = executed * bar_ret - cost - holding_cost * executed.abs()
    equity = (1.0 + strat_ret).cumprod()

    trades = int((executed.diff().fillna(0) != 0).sum())
    return BacktestResult(
        equity=equity,
        returns=strat_ret,
        position=executed,
        trades=trades,
        turnover=float(dpos.sum()),
        bars_per_year=bars_per_year,
    )
