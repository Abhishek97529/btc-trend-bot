"""
Head-to-head: our LOCKED daily spot strategy  vs  4h momentum(120,300).

Both are LONG/FLAT SPOT (no leverage, no shorts). To keep it apples-to-apples we
run BOTH over the SAME calendar window (the 4h data window: 2019-01-01 ->
2026-07-23) and the SAME cost model (0.10% fee + 5 bps slippage/side). Each runs
on its native timeframe:
    * LOCKED trend_ensemble  -> DAILY bars  (bars/yr = 365)
    * momentum(120,300)      -> 4h bars     (bars/yr = 2190)

Note: the LOCKED headline (2,326% / 47.9% CAGR) is measured from 2018-06-01. Here
we clip it to 2019-01-01 so it lines up with the 4h series — so its number here
will differ from the locked snapshot. That's intentional; we're comparing the two
strategies over IDENTICAL calendar time.

Usage:  python src/compare_locked_vs_momentum.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategies import momentum
from strategies_v2 import trend_ensemble
from backtest import run_backtest
import metrics as M
import config as C

warnings.filterwarnings("ignore")

DATA = Path(__file__).resolve().parent.parent / "data"
DATA_1H = DATA / "BTCUSDT_1h_2019-01-01_2026-07-23.parquet"
DATA_1D = DATA / "BTCUSDT_1d_2017-08-01_2026-07-23.parquet"
FEE, SLIP = C.FEE, C.SLIPPAGE
BPY_4H, BPY_1D = 6 * 365, 365
START = pd.Timestamp("2019-01-01", tz="UTC")


def load_4h() -> pd.DataFrame:
    df = pd.read_parquet(DATA_1H)
    df = df[~df.index.duplicated()].sort_index()
    o = df["open"].resample("4h").first()
    h = df["high"].resample("4h").max()
    l = df["low"].resample("4h").min()
    c = df["close"].resample("4h").last()
    v = df["volume"].resample("4h").sum()
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}).dropna()


def load_1d() -> pd.DataFrame:
    df = pd.read_parquet(DATA_1D)
    return df[~df.index.duplicated()].sort_index()


def clip(df: pd.DataFrame) -> pd.DataFrame:
    idx = df.index
    if idx.tz is None:
        start = START.tz_localize(None)
    else:
        start = START
    return df[idx >= start]


def block(ret: pd.Series, bpy: float, pos: pd.Series | None = None) -> dict:
    trades = 0 if pos is None else int((pos.diff().fillna(pos.iloc[0]).abs() > 1e-9).sum())
    expo = 100.0 if pos is None else float((pos.values > 1e-9).mean()) * 100
    return {
        "net_%": M.total_return(ret) * 100,
        "cagr_%": M.cagr(ret, bpy) * 100,
        "sharpe": M.sharpe(ret, bpy),
        "sortino": M.sortino(ret, bpy),
        "maxdd_%": M.max_drawdown(ret) * 100,
        "calmar": M.calmar(ret, bpy),
        "vol_%": M.ann_vol(ret, bpy) * 100,
        "expo_%": expo,
        "trades": trades,
    }


def yoy(ret: pd.Series) -> dict:
    return {y: M.total_return(ret[ret.index.year == y]) * 100 for y in sorted(set(ret.index.year))}


def main():
    # ---- LOCKED daily strategy (warmup BEFORE the clip so votes are valid) ---- #
    d1 = load_1d()
    sig_locked_full = trend_ensemble(d1, C.THRESHOLD)          # computed on full history
    res_locked_full = run_backtest(d1, sig_locked_full, FEE, SLIP, BPY_1D)
    # clip the RESULT series to the common window
    m1 = res_locked_full.returns.index >= START.tz_localize(None) if res_locked_full.returns.index.tz is None else res_locked_full.returns.index >= START
    r_lock = res_locked_full.returns[m1]
    p_lock = res_locked_full.position[m1]
    bh1 = run_backtest(clip(d1), pd.Series(1.0, index=clip(d1).index), FEE, SLIP, BPY_1D)
    r_bh1 = bh1.returns

    # ---- 4h momentum ---------------------------------------------------------- #
    d4 = load_4h()
    sig_mom = momentum(d4, lookback=120, trend=300)
    res_mom = run_backtest(d4, sig_mom, FEE, SLIP, BPY_4H)
    r_mom, p_mom = res_mom.returns, res_mom.position

    lock = block(r_lock, BPY_1D, p_lock)
    mom = block(r_mom, BPY_4H, p_mom)
    bh = block(r_bh1, BPY_1D)

    s = r_lock.index[0].date()
    print(f"\n{'='*82}")
    print("SPOT STRATEGY COMPARISON  (long/flat, no leverage, no shorts)")
    print(f"same window {s} -> {r_lock.index[-1].date()}   |   costs {FEE*100:.2f}%+{SLIP*100:.3f}%/side")
    print(f"{'='*82}")
    print(f"  {'metric':<18}{'LOCKED daily':>15}{'4h momentum':>15}{'buy & hold':>14}")
    print("  " + "-" * 62)
    labels = [("net_%", "total return %"), ("cagr_%", "CAGR %"), ("sharpe", "Sharpe"),
              ("sortino", "Sortino"), ("maxdd_%", "max drawdown %"), ("calmar", "Calmar"),
              ("vol_%", "ann vol %"), ("expo_%", "time in mkt %"), ("trades", "trades")]
    for k, lab in labels:
        if k == "trades":
            print(f"  {lab:<18}{lock[k]:>15,d}{mom[k]:>15,d}{bh[k]:>14,d}")
        else:
            print(f"  {lab:<18}{lock[k]:>15,.1f}{mom[k]:>15,.1f}{bh[k]:>14,.1f}")

    # growth of 1 lakh
    g_lock = float((1 + r_lock).prod())
    g_mom = float((1 + r_mom).prod())
    g_bh = float((1 + r_bh1).prod())
    print("  " + "-" * 62)
    print(f"  {'Rs 1L becomes':<18}{100000*g_lock:>15,.0f}{100000*g_mom:>15,.0f}{100000*g_bh:>14,.0f}")

    # ---- YEAR-BY-YEAR --------------------------------------------------------- #
    yl, ym, yb = yoy(r_lock), yoy(r_mom), yoy(r_bh1)
    years = sorted(set(yl) | set(ym))
    print(f"\n{'='*82}")
    print("YEAR-BY-YEAR total return %  (compounded within each calendar year)")
    print(f"{'='*82}")
    print(f"  {'year':<6}{'LOCKED daily':>15}{'4h momentum':>15}{'buy & hold':>14}{'winner':>16}")
    print("  " + "-" * 66)
    lock_wins = mom_wins = 0
    for y in years:
        a, b, c = yl.get(y, float('nan')), ym.get(y, float('nan')), yb.get(y, float('nan'))
        trio = {"LOCKED": a, "4h mom": b, "B&H": c}
        winner = max(trio, key=lambda k: (trio[k] if trio[k] == trio[k] else -1e9))
        if winner == "LOCKED":
            lock_wins += 1
        elif winner == "4h mom":
            mom_wins += 1
        print(f"  {y:<6}{a:>15,.1f}{b:>15,.1f}{c:>14,.1f}{winner:>16}")
    print("  " + "-" * 66)
    print(f"  best-of-3 years:  LOCKED {lock_wins}   |   4h momentum {mom_wins}   |   B&H {len(years)-lock_wins-mom_wins}")
    print(f"{'='*82}\n")


if __name__ == "__main__":
    main()
