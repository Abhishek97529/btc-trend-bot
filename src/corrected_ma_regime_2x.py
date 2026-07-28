"""
Corrected audit and walk-forward optimization for BTCUSDT ma_regime at 2x.

Unlike the older vector backtest, this is a stateful futures account:
  * a signal observed at close[t-1] trades at open[t];
  * contract quantity is fixed until the next entry/exit (no free 4h re-levering);
  * fees and slippage apply to actual entry/exit notional;
  * real funding changes wallet balance at its timestamp;
  * liquidation uses cumulative PnL from entry against mark-price OHLC;
  * each walk-forward test fold starts flat and pays its own entry cost.

This is still research, not an exchange-exact liquidation calculator. Binance
maintenance tiers, insurance/ADL, latency, spread spikes, and taxes are omitted.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M
from indicators import sma
from perp_walkforward import load

BPY = 6 * 365
LEVERAGE = 2.0
FEE = 0.0004
SLIPPAGE = 0.0003
MM = 0.005


def regime(df: pd.DataFrame, ma: int, buffer: float) -> pd.Series:
    """Long above SMA*(1+buffer), flat otherwise; causal at the bar close."""
    line = sma(df["close"], ma)
    return ((df["close"] > line * (1.0 + buffer)) & line.notna()).astype(float)


@dataclass
class AccountResult:
    returns: pd.Series
    equity: pd.Series
    position: pd.Series
    entries: int
    exits: int
    liquidated: bool
    liquidation_time: pd.Timestamp | None
    fees: float
    funding: float


def simulate(
    df: pd.DataFrame,
    target: pd.Series,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    leverage: float = LEVERAGE,
    fee: float = FEE,
    slippage: float = SLIPPAGE,
    maintenance_margin: float = MM,
    charge_funding: bool = True,
    funding_multiplier: float = 1.0,
    mark_shock: float = 0.0,
) -> AccountResult:
    """Run an isolated, all-equity account. Every requested window starts flat."""
    use = df.loc[start:end] if start is not None or end is not None else df
    if use.empty:
        raise ValueError("empty simulation window")

    wallet = 1.0
    qty = 0.0
    entry_price = 0.0
    previous_equity = 1.0
    entries = exits = 0
    fees_paid = funding_paid = 0.0
    liquidated = False
    liquidation_time = None
    out_ret, out_eq, out_pos = [], [], []
    full_index = df.index

    for ts, row in use.iterrows():
        loc = full_index.get_loc(ts)
        desired = 0.0 if loc == 0 else float(target.iloc[loc - 1] > 0.5)
        open_px = float(row["open"])
        raw_mark_open = row.get("mark_open", open_px)
        raw_mark_low = row.get("mark_low", row["low"])
        mark_open = open_px if pd.isna(raw_mark_open) else float(raw_mark_open)
        mark_low = float(row["low"]) if pd.isna(raw_mark_low) else float(raw_mark_low)
        if mark_shock > 0:
            mark_low = min(mark_low, mark_open * (1.0 - mark_shock))
        close_px = float(row["close"])

        # The prior close signal is executed at this bar's open.
        if qty != 0.0 and desired == 0.0:
            wallet += qty * (open_px - entry_price)
            charge = abs(qty * open_px) * (fee + slippage)
            wallet -= charge
            fees_paid += charge
            qty = 0.0
            entry_price = 0.0
            exits += 1
        elif qty == 0.0 and desired == 1.0:
            # Solve notional = L * equity after entry cost.
            notional = leverage * wallet / (1.0 + leverage * (fee + slippage))
            charge = notional * (fee + slippage)
            wallet -= charge
            fees_paid += charge
            qty = notional / open_px
            entry_price = open_px
            entries += 1

        # Funding is signed: positive rates cost longs, negative rates credit them.
        if qty != 0.0:
            payment = (qty * mark_open * float(row["funding"]) * funding_multiplier
                       if charge_funding else 0.0)
            wallet -= payment
            funding_paid += payment

            # Long liquidation: wallet + unrealized PnL <= maintenance notional.
            low_equity = wallet + qty * (mark_low - entry_price)
            maintenance = maintenance_margin * abs(qty) * mark_low
            if low_equity <= maintenance:
                wallet = 0.0
                qty = 0.0
                entry_price = 0.0
                liquidated = True
                liquidation_time = ts

        equity = wallet if qty == 0.0 else wallet + qty * (close_px - entry_price)
        equity = max(equity, 0.0)
        bar_return = equity / previous_equity - 1.0 if previous_equity > 0 else 0.0
        out_ret.append(bar_return)
        out_eq.append(equity)
        out_pos.append(0.0 if qty == 0.0 or equity <= 0 else qty * close_px / equity)
        previous_equity = equity

        if liquidated:
            # Preserve a complete zero-equity series for the requested window.
            remaining = len(use) - len(out_ret)
            out_ret.extend([0.0] * remaining)
            out_eq.extend([0.0] * remaining)
            out_pos.extend([0.0] * remaining)
            break

    idx = use.index
    return AccountResult(
        pd.Series(out_ret, index=idx),
        pd.Series(out_eq, index=idx),
        pd.Series(out_pos, index=idx),
        entries,
        exits,
        liquidated,
        liquidation_time,
        fees_paid,
        funding_paid,
    )


def stats(result: AccountResult) -> dict:
    r = result.returns
    return {
        "net_%": M.total_return(r) * 100,
        "cagr_%": M.cagr(r, BPY) * 100,
        "sharpe": M.sharpe(r, BPY),
        "maxdd_%": M.max_drawdown(r) * 100,
        "calmar": M.calmar(r, BPY),
    }


GRID = [(ma, buf) for ma in (150, 200, 250, 300, 350, 400)
        for buf in (0.00, 0.01, 0.02, 0.03)]


def walk_forward(df: pd.DataFrame):
    """24-month train, 6-month test. Optimize only on past Sharpe/Calmar."""
    train_bars = 2 * BPY
    test_bars = BPY // 2
    cursor = train_bars
    stitched = []
    folds = []
    while cursor < len(df):
        tr_start = df.index[cursor - train_bars]
        tr_end = df.index[cursor - 1]
        te_start = df.index[cursor]
        te_end = df.index[min(cursor + test_bars, len(df)) - 1]
        ranked = []
        for ma, buf in GRID:
            sig = regime(df, ma, buf)
            result = simulate(df, sig, tr_start, tr_end)
            s = stats(result)
            if not result.liquidated:
                ranked.append((round(s["sharpe"], 6), round(s["calmar"], 6), ma, buf))
        if not ranked:
            raise RuntimeError(f"all training configurations liquidated at {tr_end}")
        _, _, ma, buf = max(ranked)
        result = simulate(df, regime(df, ma, buf), te_start, te_end)
        s = stats(result)
        folds.append((te_start, te_end, ma, buf, s, result.liquidated))
        stitched.append(result.returns)
        cursor += test_bars
    return pd.concat(stitched), folds


def print_row(name: str, s: dict, result: AccountResult):
    liq = str(result.liquidation_time) if result.liquidated else "-"
    print(f"{name:<25}{s['net_%']:>12,.1f}{s['cagr_%']:>10.2f}{s['sharpe']:>10.2f}"
          f"{s['maxdd_%']:>11.2f}{s['calmar']:>10.2f}{result.entries:>9}{liq:>27}")


def main():
    df = load()
    print(f"\nCorrected stateful BTCUSDT perpetual test: {df.index[0]} -> {df.index[-1]}")
    print(f"2x, fee={FEE*100:.2f}%, slip={SLIPPAGE*100:.2f}%, MM={MM*100:.2f}%\n")
    print(f"{'configuration':<25}{'net %':>12}{'CAGR %':>10}{'Sharpe':>10}"
          f"{'maxDD %':>11}{'Calmar':>10}{'entries':>9}{'liquidation':>27}")
    print("-" * 114)

    fixed = simulate(df, regime(df, 250, 0.0))
    print_row("fixed MA250 buffer0", stats(fixed), fixed)

    oos, folds = walk_forward(df)
    oos_start, oos_end = oos.index[0], oos.index[-1]
    fixed_oos = simulate(df, regime(df, 250, 0.0), oos_start, oos_end)
    print_row("fixed MA250 same OOS", stats(fixed_oos), fixed_oos)
    os = {
        "net_%": M.total_return(oos) * 100,
        "cagr_%": M.cagr(oos, BPY) * 100,
        "sharpe": M.sharpe(oos, BPY),
        "maxdd_%": M.max_drawdown(oos) * 100,
        "calmar": M.calmar(oos, BPY),
    }
    dummy = AccountResult(oos, (1 + oos).cumprod(), pd.Series(0.0, index=oos.index),
                          sum(1 for _ in folds), 0, any(x[-1] for x in folds), None, 0, 0)
    print_row("walk-forward optimized", os, dummy)

    print("\nWalk-forward folds (chosen using preceding 24 months only):")
    for lo, hi, ma, buf, s, liq in folds:
        print(f"  {lo.date()}->{hi.date()}  MA={ma:<3} buffer={buf:>4.0%}  "
              f"net={s['net_%']:>7.1f}%  Sh={s['sharpe']:>5.2f}  "
              f"DD={s['maxdd_%']:>6.1f}%  liq={liq}")


if __name__ == "__main__":
    main()
