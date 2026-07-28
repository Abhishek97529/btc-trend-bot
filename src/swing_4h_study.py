"""
4-HOUR SWING STUDY — can any 4h swing strategy beat BTC/USDT buy & hold?

Resamples the 1h history to 4h bars and runs the candidate long/flat strategies
(spot-deployable, no leverage/shorts) through the SAME backtest engine + realistic
costs (0.10% fee + 5 bps slippage per side). Two passes:

  PART A — full-sample ranking of one sensible config per strategy vs buy & hold.
  PART B — HONEST out-of-sample check: pick each strategy's best params on the
           first 60% of history, then measure how it does on the untouched last
           40%. Beating B&H in-sample is easy; surviving OOS is the real test.

Costs matter enormously at 4h: a strategy that flips weekly pays ~50-100 round
trips/yr. That's the headwind every result below is fighting.

Usage:  python src/swing_4h_study.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategies import ema_crossover, donchian_breakout, rsi_mean_reversion, momentum, trend_vol_target
from strategies_v2 import trend_ensemble, vol_target_trend, ma_regime
from backtest import run_backtest
import metrics as M

warnings.filterwarnings("ignore")

DATA_1H = Path(__file__).resolve().parent.parent / "data" / "BTCUSDT_1h_2019-01-01_2026-07-23.parquet"
FEE, SLIP = 0.001, 0.0005
BPY = 6 * 365                    # 4h bars per year ≈ 2190


def load_4h() -> pd.DataFrame:
    df = pd.read_parquet(DATA_1H)
    df = df[~df.index.duplicated()].sort_index()
    o = df["open"].resample("4h").first()
    h = df["high"].resample("4h").max()
    l = df["low"].resample("4h").min()
    c = df["close"].resample("4h").last()
    v = df["volume"].resample("4h").sum()
    out = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}).dropna()
    return out


def evaluate(sig: pd.Series, df: pd.DataFrame, mask: pd.Series | None = None) -> dict:
    """Run the engine; if mask given, compute metrics on that sub-window only."""
    res = run_backtest(df, sig, FEE, SLIP, BPY)
    ret, pos = res.returns, res.position
    if mask is not None:
        ret, pos = ret[mask], pos[mask]
    trades = int((pos.diff().fillna(pos.iloc[0]).abs() > 1e-9).sum())
    expo = float((pos.values > 1e-9).mean())
    s = M.summary(ret, BPY, trades, expo)
    s["net_%"] = M.total_return(ret) * 100
    return s


# One curated config per strategy. Windows are in 4h BARS (so 6 = 1 day).
# These are "swing" horizons: days-to-weeks holds, not scalps.
CANDIDATES = {
    "ema_crossover(12,48)":     lambda d: ema_crossover(d, fast=12, slow=48),      # 2d / 8d
    "ema_crossover(24,96)":     lambda d: ema_crossover(d, fast=24, slow=96),      # 4d / 16d
    "donchian(60,30)":          lambda d: donchian_breakout(d, entry=60, exit_n=30),
    "donchian(120,60)":         lambda d: donchian_breakout(d, entry=120, exit_n=60),
    "momentum(60,200)":         lambda d: momentum(d, lookback=60, trend=200),
    "momentum(120,300)":        lambda d: momentum(d, lookback=120, trend=300),
    "rsi_meanrev(14,30/55)":    lambda d: rsi_mean_reversion(d, n=14, lower=30, upper=55, trend=200),
    "trend_vol_target":         lambda d: trend_vol_target(d, trend=200, atr_n=48, target_atr_pct=0.02),
    "trend_ensemble(0.5)":      lambda d: trend_ensemble(d, 0.5),
    "vol_target_trend":         lambda d: vol_target_trend(d, target_vol=0.6, vol_win=30, bars_per_year=BPY),
    "ma_regime(200)":           lambda d: ma_regime(d, ma=200),
}

# Small per-family grids for the OOS pass (kept modest to limit selection bias).
GRIDS = {
    "ema_crossover": (ema_crossover, [{"fast": f, "slow": s} for f in (12, 24, 48) for s in (48, 96, 168) if s > f]),
    "donchian_breakout": (donchian_breakout, [{"entry": e, "exit_n": x} for e in (60, 120, 240) for x in (30, 60)]),
    "momentum": (momentum, [{"lookback": lb, "trend": tr} for lb in (30, 60, 120, 180) for tr in (200, 300)]),
    "trend_ensemble": (trend_ensemble, [{"threshold": t} for t in (0.4, 0.5, 0.6, 0.7)]),
    "ma_regime": (ma_regime, [{"ma": m} for m in (100, 150, 200, 250)]),
}


def fmt_row(name, s):
    return (f"  {name:<24} {s['net_%']:>10,.0f}  {s['cagr']*100:>7.1f}  {s['sharpe']:>6.2f}  "
            f"{s['sortino']:>7.2f}  {s['max_drawdown']*100:>7.1f}  {s['calmar']:>6.2f}  "
            f"{s['trades']:>6}  {s['exposure']*100:>5.0f}")


def main():
    df = load_4h()
    print(f"\n4h bars: {len(df)}   {df.index[0].date()} -> {df.index[-1].date()}   "
          f"(costs {FEE*100:.2f}%+{SLIP*100:.3f}%/side, {BPY} bars/yr)")

    bh = pd.Series(1.0, index=df.index)
    bh_s = evaluate(bh, df)

    hdr = (f"  {'strategy':<24} {'net_%':>10}  {'CAGR%':>7}  {'Sharpe':>6}  "
           f"{'Sortino':>7}  {'maxDD%':>7}  {'Calmar':>6}  {'trades':>6}  {'in%':>5}")

    print("\n" + "=" * 100)
    print("PART A — FULL SAMPLE (one curated config per strategy) vs BUY & HOLD")
    print("=" * 100)
    print(hdr)
    print("  " + "-" * 96)
    print(fmt_row("BUY & HOLD", bh_s))
    print("  " + "-" * 96)
    rows = []
    for name, fn in CANDIDATES.items():
        try:
            s = evaluate(fn(df), df)
            rows.append((name, s))
        except Exception as e:
            print(f"  {name:<24} ERROR: {e}")
    # rank by Sharpe
    for name, s in sorted(rows, key=lambda kv: kv[1]["sharpe"], reverse=True):
        beat = "  <-- beats B&H net" if s["net_%"] > bh_s["net_%"] else ""
        print(fmt_row(name, s) + beat)

    print("\n  Read: 'net_%' = total return over the whole sample. B&H is the bar to clear on")
    print("  BOTH return AND risk (Sharpe/maxDD). Higher Sharpe with far smaller drawdown can")
    print("  be 'better' even below B&H raw return — but the headline question is raw return.")

    # ---- PART B: out-of-sample -------------------------------------------- #
    split = df.index[int(len(df) * 0.60)]
    train = df.index < split
    test = df.index >= split
    bh_train = evaluate(bh, df, train)
    bh_test = evaluate(bh, df, test)

    print("\n" + "=" * 100)
    print(f"PART B — OUT-OF-SAMPLE:  train < {split.date()}  |  test >= {split.date()}")
    print("  (pick each family's best params by TRAIN Sharpe, then report untouched TEST)")
    print("=" * 100)
    print(f"  {'strategy (best-in-train cfg)':<34} {'TRAIN Sh':>9} {'TEST net%':>10} {'TEST CAGR%':>11} "
          f"{'TEST Sh':>8} {'TEST maxDD%':>12}")
    print("  " + "-" * 92)
    print(f"  {'BUY & HOLD':<34} {bh_train['sharpe']:>9.2f} {bh_test['net_%']:>10,.0f} "
          f"{bh_test['cagr']*100:>11.1f} {bh_test['sharpe']:>8.2f} {bh_test['max_drawdown']*100:>12.1f}")
    print("  " + "-" * 92)

    winners = 0
    for fam, (fn, grid) in GRIDS.items():
        best, best_sh = None, -1e9
        for params in grid:
            try:
                s_tr = evaluate(fn(df, **params), df, train)
            except Exception:
                continue
            if s_tr["sharpe"] > best_sh:
                best_sh, best = s_tr["sharpe"], params
        if best is None:
            continue
        s_te = evaluate(fn(df, **best), df, test)
        cfg = ",".join(f"{k}={v}" for k, v in best.items())
        beat = ""
        if s_te["net_%"] > bh_test["net_%"]:
            beat = "  <-- beats B&H OOS"
            winners += 1
        print(f"  {fam+' ('+cfg+')':<34} {best_sh:>9.2f} {s_te['net_%']:>10,.0f} "
              f"{s_te['cagr']*100:>11.1f} {s_te['sharpe']:>8.2f} {s_te['max_drawdown']*100:>12.1f}{beat}")

    print("\n" + "=" * 100)
    print(f"VERDICT: {winners}/{len(GRIDS)} strategy families beat B&H on RAW RETURN out-of-sample.")
    print("  Compare Sharpe & maxDD columns too — a lower-return, much-higher-Sharpe, shallower-")
    print("  drawdown strategy may still be the better RISK-ADJUSTED bet even if it trails on raw %.")
    print("=" * 100 + "\n")


if __name__ == "__main__":
    main()
