"""
momentum(120,300) on 4h BTC/USDT — full breakdown incl. YEAR-BY-YEAR returns.

WHAT THIS STRATEGY IS (important):
  momentum(lookback=120, trend=300) on 4h bars means:
    * lookback 120 bars  = 20 days of rate-of-change
    * trend    300 bars  = 50 days simple moving average
  Rule:  hold 100% BTC when  (20d ROC > 0)  AND  (price > 50d MA);  else 100% CASH.
  Position is BINARY {0.0, 1.0}. It is LONG/FLAT SPOT — no leverage, no shorting,
  no futures, no funding cost. Max exposure = 1.0x. Same as buy&hold's risk cap,
  just sitting in cash during downtrends.

Costs: 0.10% fee + 5 bps slippage per side, charged on turnover. No lookahead
(engine executes target.shift(1)).

Usage:  python src/momentum_4h_report.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from legacy_strategies import momentum
from backtest import run_backtest
import metrics as M

warnings.filterwarnings("ignore")

DATA_1H = Path(__file__).resolve().parent.parent / "data" / "BTCUSDT_1h_2019-01-01_2026-07-23.parquet"
FEE, SLIP = 0.001, 0.0005
BPY = 6 * 365                    # 4h bars per year ≈ 2190
LOOKBACK, TREND = 120, 300       # in 4h bars (20d ROC, 50d MA)


def load_4h() -> pd.DataFrame:
    df = pd.read_parquet(DATA_1H)
    df = df[~df.index.duplicated()].sort_index()
    o = df["open"].resample("4h").first()
    h = df["high"].resample("4h").max()
    l = df["low"].resample("4h").min()
    c = df["close"].resample("4h").last()
    v = df["volume"].resample("4h").sum()
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}).dropna()


def block(ret: pd.Series, pos: pd.Series | None = None) -> dict:
    trades = 0 if pos is None else int((pos.diff().fillna(pos.iloc[0]).abs() > 1e-9).sum())
    expo = 1.0 if pos is None else float((pos.values > 1e-9).mean())
    return {
        "net_%": M.total_return(ret) * 100,
        "cagr_%": M.cagr(ret, BPY) * 100,
        "sharpe": M.sharpe(ret, BPY),
        "sortino": M.sortino(ret, BPY),
        "maxdd_%": M.max_drawdown(ret) * 100,
        "calmar": M.calmar(ret, BPY),
        "vol_%": M.ann_vol(ret, BPY) * 100,
        "trades": trades,
        "expo_%": expo * 100,
    }


def main():
    df = load_4h()

    # Strategy vs buy & hold through the same engine.
    sig = momentum(df, lookback=LOOKBACK, trend=TREND)
    res = run_backtest(df, sig, FEE, SLIP, BPY)
    bh = run_backtest(df, pd.Series(1.0, index=df.index), FEE, SLIP, BPY)

    r_str, p_str = res.returns, res.position
    r_bh = bh.returns

    print(f"\n{'='*78}")
    print("momentum(120,300) on 4h BTC/USDT   —   SPOT LONG/FLAT, NO LEVERAGE, NO SHORTS")
    print(f"{'='*78}")
    print(f"data: {len(df)} 4h bars   {df.index[0].date()} -> {df.index[-1].date()}")
    print(f"rule: 100% BTC when 20d-ROC>0 AND price>50d-MA, else 100% cash  (binary 0/1)")
    print(f"costs: {FEE*100:.2f}% fee + {SLIP*100:.3f}% slippage per side\n")

    s = block(r_str, p_str)
    b = block(r_bh)
    print(f"  {'metric':<16}{'momentum(120,300)':>20}{'buy & hold':>16}")
    print("  " + "-" * 52)
    for k, lab in [("net_%", "total return %"), ("cagr_%", "CAGR %"),
                   ("sharpe", "Sharpe"), ("sortino", "Sortino"),
                   ("maxdd_%", "max drawdown %"), ("calmar", "Calmar"),
                   ("vol_%", "ann vol %"), ("expo_%", "time in market %"),
                   ("trades", "trades")]:
        fs = f"{s[k]:>20,.1f}" if k not in ("trades",) else f"{s[k]:>20,d}"
        fb = f"{b[k]:>16,.1f}" if k not in ("trades",) else f"{b[k]:>16,d}"
        print(f"  {lab:<16}{fs}{fb}")

    # ---- YEAR-BY-YEAR (calendar) ----------------------------------------- #
    print(f"\n{'='*78}")
    print("YEAR-BY-YEAR (calendar year, compounded within the year)")
    print(f"{'='*78}")
    print(f"  {'year':<6}{'strat %':>10}{'B&H %':>10}{'edge pp':>9}{'in-mkt%':>9}"
          f"{'trades':>8}{'strat maxDD%':>13}{'result':>9}")
    print("  " + "-" * 72)

    years = sorted(set(df.index.year))
    strat_wins = 0
    for y in years:
        m = r_str.index.year == y
        rs, rb, ps = r_str[m], r_bh[m], p_str[m]
        sr = M.total_return(rs) * 100
        br = M.total_return(rb) * 100
        expo = float((ps.values > 1e-9).mean()) * 100
        tr = int((ps.diff().fillna(ps.iloc[0]).abs() > 1e-9).sum())
        dd = M.max_drawdown(rs) * 100
        win = "WIN" if sr > br else "lose"
        if sr > br:
            strat_wins += 1
        print(f"  {y:<6}{sr:>10,.1f}{br:>10,.1f}{sr-br:>9,.1f}{expo:>9,.0f}"
              f"{tr:>8}{dd:>13,.1f}{win:>9}")

    print("  " + "-" * 72)
    print(f"  strategy beat B&H in {strat_wins}/{len(years)} calendar years")

    # Best / worst calendar year for the strategy.
    yr_ret = {y: M.total_return(r_str[r_str.index.year == y]) * 100 for y in years}
    by = max(yr_ret, key=yr_ret.get)
    wy = min(yr_ret, key=yr_ret.get)
    print(f"  best year:  {by}  {yr_ret[by]:+,.1f}%     worst year: {wy}  {yr_ret[wy]:+,.1f}%")

    # ---- growth of 1 lakh (₹) reference ---------------------------------- #
    print(f"\n{'='*78}")
    print("GROWTH OF Rs 1,00,000 (pre-tax, pre-real-fee; illustration)")
    print(f"{'='*78}")
    eq_s = float((1 + r_str).prod())
    eq_b = float((1 + r_bh).prod())
    print(f"  momentum(120,300):  Rs {100000*eq_s:>14,.0f}   ({eq_s:.2f}x)")
    print(f"  buy & hold:         Rs {100000*eq_b:>14,.0f}   ({eq_b:.2f}x)")
    print(f"{'='*78}\n")


if __name__ == "__main__":
    main()
