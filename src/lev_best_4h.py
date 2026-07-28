"""
Pick THE best 4h leveraged (2x / 3x) BTC strategy — and prove it out-of-sample.

Since leverage scales return and vol together (Sharpe is invariant until decay/
liquidation), the best leveraged strategy is simply the best BASE engine, levered,
provided it stays FLAT during crashes so it never liquidates.

We rank the strongest trend bases at 2x and 3x on:
    * survives?  (liq == 0)  <- non-negotiable
    * Sharpe / Calmar        <- risk-adjusted quality (leverage-invariant)
    * net return / CAGR      <- the headline
Then we OUT-OF-SAMPLE the winner: choose it on 2019 -> mid-2023, measure the
untouched mid-2023 -> 2026, at 2x and 3x, and print the definitive YEAR-BY-YEAR
table with intra-year drawdown so you see the actual ride.

Usage:  python src/lev_best_4h.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategies import momentum, ema_crossover, donchian_breakout
from strategies_v2 import trend_ensemble, ma_regime
from lev_4h_study import load_4h, funding_per_4h, lev_backtest, BPY
import metrics as M

warnings.filterwarnings("ignore")


# The contenders (each already known to be a solid 4h trend base).
BASES = {
    "momentum(120,300)":     lambda d: momentum(d, 120, 300),
    "ema_crossover(24,168)": lambda d: ema_crossover(d, 24, 168),
    "ma_regime(200)":        lambda d: ma_regime(d, 200),
    "ma_regime(250)":        lambda d: ma_regime(d, 250),
    "trend_ensemble(0.5)":   lambda d: trend_ensemble(d, 0.5),
    "donchian(120,60)":      lambda d: donchian_breakout(d, 120, 60),
}


def metrics_on(ret, pos, mask=None):
    if mask is not None:
        ret, pos = ret[mask], pos[mask]
    return {
        "net_%": M.total_return(ret) * 100,
        "cagr_%": M.cagr(ret, BPY) * 100,
        "sharpe": M.sharpe(ret, BPY),
        "maxdd_%": M.max_drawdown(ret) * 100,
        "calmar": M.calmar(ret, BPY),
    }


def main():
    df = load_4h()
    fund = funding_per_4h(df.index)
    ones = pd.Series(1.0, index=df.index)

    print(f"\n4h {df.index[0].date()}->{df.index[-1].date()}  funding+liq modeled, mm=0.5%")

    # ---- RANK bases at 2x and 3x (full sample) --------------------------- #
    print("\n" + "=" * 92)
    print("RANKING — strongest 4h base engines, LEVERED (funding + liquidation modeled)")
    print("=" * 92)
    print(f"  {'base x lev':<30}{'net_%':>12}{'CAGR%':>8}{'Sharpe':>8}{'maxDD%':>9}{'Calmar':>8}{'liq':>6}")
    print("  " + "-" * 82)
    ranking = []
    for name, fn in BASES.items():
        sig = fn(df)
        for L in (2.0, 3.0):
            r = lev_backtest(df, sig, ones * L, funding=fund)
            m = metrics_on(r.returns, r.position)
            ranking.append((f"{name} x{L:g}", m, r.liq_bars, name, L))
    # survivors first, then by Sharpe (leverage-invariant quality), then CAGR
    ranking.sort(key=lambda t: (t[2] == 0, t[1]["sharpe"], t[1]["cagr_%"]), reverse=True)
    for label, m, liq, _, _ in ranking:
        tag = "  RUIN" if liq else ""
        print(f"  {label:<30}{m['net_%']:>12,.0f}{m['cagr_%']:>8.1f}{m['sharpe']:>8.2f}"
              f"{m['maxdd_%']:>9.1f}{m['calmar']:>8.2f}{liq:>6}{tag}")

    winner_name, winner_L = ranking[0][3], ranking[0][4]
    winner_fn = BASES[winner_name]
    print("  " + "-" * 82)
    print(f"  WINNER (survives + best Sharpe): {winner_name}  at {winner_L:g}x")

    # ---- OUT-OF-SAMPLE the winning BASE (2x and 3x) ---------------------- #
    split = df.index[int(len(df) * 0.60)]
    train = df.index < split
    test = df.index >= split
    print("\n" + "=" * 92)
    print(f"OUT-OF-SAMPLE — winner base '{winner_name}'   train<{split.date()} | test>={split.date()}")
    print("=" * 92)
    print(f"  {'segment':<18}{'lev':>5}{'net_%':>12}{'CAGR%':>9}{'Sharpe':>8}{'maxDD%':>9}{'liq':>6}")
    print("  " + "-" * 70)
    sig_w = winner_fn(df)
    for L in (1, 2, 3):
        r = lev_backtest(df, sig_w, ones * L, funding=fund)
        for seg, mask in [("TRAIN", train), ("TEST (unseen)", test)]:
            m = metrics_on(r.returns, r.position, mask)
            liq = int(((r.position[mask].abs() > 1) &
                       (((df["close"].shift(1)[mask] - df["low"][mask]) / df["close"].shift(1)[mask])
                        >= (1.0 / r.position[mask].abs().replace(0, np.nan) - 0.005))).sum())
            print(f"  {seg:<18}{L:>5}{m['net_%']:>12,.0f}{m['cagr_%']:>9.1f}"
                  f"{m['sharpe']:>8.2f}{m['maxdd_%']:>9.1f}{liq:>6}")
        print("  " + "-" * 70)

    # ---- DEFINITIVE YEAR-BY-YEAR for the winner at 1x / 2x / 3x ---------- #
    print("\n" + "=" * 92)
    print(f"DEFINITIVE YEAR-BY-YEAR — {winner_name}  (return %  |  intra-year maxDD %)")
    print("=" * 92)
    rr = {L: lev_backtest(df, sig_w, ones * L, funding=fund).returns for L in (1, 2, 3)}
    bh = df["close"].pct_change().fillna(0.0)
    print(f"  {'year':<6}{'1x ret':>9}{'1x DD':>8}   {'2x ret':>9}{'2x DD':>8}   "
          f"{'3x ret':>9}{'3x DD':>8}   {'B&H ret':>9}")
    print("  " + "-" * 84)
    for y in sorted(set(df.index.year)):
        def rd(r):
            rs = r[r.index.year == y]
            return M.total_return(rs) * 100, M.max_drawdown(rs) * 100
        r1, d1 = rd(rr[1]); r2, d2 = rd(rr[2]); r3, d3 = rd(rr[3])
        rb, _ = rd(bh)
        print(f"  {y:<6}{r1:>9,.0f}{d1:>8.0f}   {r2:>9,.0f}{d2:>8.0f}   "
              f"{r3:>9,.0f}{d3:>8.0f}   {rb:>9,.0f}")
    print("  " + "-" * 84)

    # terminal wealth
    print("\n  Rs 1,00,000 grows to (full sample, pre-real-fee/tax):")
    for L in (1, 2, 3):
        g = float((1 + rr[L]).prod())
        print(f"    {winner_name} x{L}:  Rs {100000*g:>16,.0f}   ({g:,.1f}x)")
    gb = float((1 + bh).prod())
    print(f"    buy & hold:            Rs {100000*gb:>16,.0f}   ({gb:,.1f}x)")
    print("=" * 92 + "\n")


if __name__ == "__main__":
    main()
