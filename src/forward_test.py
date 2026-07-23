"""
Strict out-of-sample FORWARD test.

The strategy has NO fitted parameters (all windows fixed, threshold=0.5 fixed), so
the honest forward test is: stand at a past cutoff date and judge the strategy ONLY
on the data that came AFTER it -- data that played no role in choosing anything.

For several cutoffs we split the record into:
  * IN-SAMPLE   (design era, up to the cutoff)
  * FORWARD     (everything after the cutoff -- genuinely unseen)
and compare the strategy to buy & hold on the FORWARD slice with full metrics.

Usage:  python src/forward_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_fetch import fetch_klines
from strategies_v2 import trend_ensemble
from backtest import run_backtest
import metrics as M

FEE, SLIP = 0.001, 0.0005
BPY = 365
CUTOFFS = ["2020-12-31", "2021-12-31", "2022-12-31", "2023-12-31"]


def seg_stats(net, bh, pos):
    in_mkt = pos.abs() > 1e-9
    prev = in_mkt.shift(1, fill_value=False)
    trades = int((in_mkt & ~prev).sum())
    s = M.summary(net, BPY)
    b = M.summary(bh, BPY)
    return s, b, trades


def show(tag, s, b, trades, ndays):
    print(f"  {tag}")
    print(f"    strategy : ret {s['total_return']*100:8.1f}%   cagr {s['cagr']*100:6.1f}%   "
          f"sharpe {s['sharpe']:5.2f}   maxDD {s['max_drawdown']*100:6.1f}%   trades {trades}")
    print(f"    buy&hold : ret {b['total_return']*100:8.1f}%   cagr {b['cagr']*100:6.1f}%   "
          f"sharpe {b['sharpe']:5.2f}   maxDD {b['max_drawdown']*100:6.1f}%")
    print(f"    -> edge  : return {(s['total_return']-b['total_return'])*100:+.1f}pp   "
          f"sharpe {s['sharpe']-b['sharpe']:+.2f}   "
          f"drawdown {(s['max_drawdown']-b['max_drawdown'])*100:+.1f}pp   ({ndays} days)")


def main():
    # IMPORTANT: compute the signal on the FULL series (indicators need warmup history),
    # then slice returns to the forward window. Nothing about the strategy is fit to data,
    # so this is a valid forward test -- we're only choosing the evaluation window.
    df = fetch_klines("BTCUSDT", "1d", "2017-08-01")
    df = df[~df.index.duplicated()].sort_index().loc["2018-06-01":]
    sig = trend_ensemble(df, threshold=0.5)
    res = run_backtest(df, sig, fee=FEE, slippage=SLIP, bars_per_year=BPY)
    net = res.returns
    bh = df["close"].pct_change().fillna(0.0)
    pos = res.position

    print(f"\nOUT-OF-SAMPLE FORWARD TEST | data {df.index[0].date()} -> {df.index[-1].date()}")
    print("Strategy params are fixed (never fit), so post-cutoff data is genuinely unseen.\n")

    for cut in CUTOFFS:
        fwd = pd.Timestamp(cut, tz="UTC") + pd.Timedelta(days=1)
        f_net, f_bh, f_pos = net.loc[fwd:], bh.loc[fwd:], pos.loc[fwd:]
        if len(f_net) < 30:
            continue
        s, b, tr = seg_stats(f_net, f_bh, f_pos)
        print(f"=== 'Standing at {cut}' -> forward window {fwd.date()} .. {df.index[-1].date()} ===")
        show("FORWARD (unseen)", s, b, tr, len(f_net))
        print()

    # full-period reference
    s, b, tr = seg_stats(net, bh, pos)
    print("=== FULL PERIOD (reference) ===")
    show("full", s, b, tr, len(net))


if __name__ == "__main__":
    main()
