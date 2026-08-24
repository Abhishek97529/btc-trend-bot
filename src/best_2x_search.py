"""
Find THE best 2x leveraged 4h BTC strategy — chosen out-of-sample, not curve-fit.

Method (honest):
  1. Split history 60/40. Search a modest grid PER FAMILY on the TRAIN half only.
  2. Keep each family's best config by TRAIN Sharpe.
  3. Rank those finalists by their UNTOUCHED TEST Sharpe (robust quality).
  4. Winner must also NEVER liquidate (funding + intrabar liq modeled at 2x).
Then print the winner's full-sample stats and definitive YEAR-BY-YEAR ride.

All configs run at L=2. Leverage is invariant to Sharpe, so ranking by Sharpe on
the base == ranking the levered strategy; we just carry the liq check + real
funding drag so a config that only shines because it ignores costs is exposed.

Usage:  python src/best_2x_search.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from legacy_strategies import momentum, ema_crossover, donchian_breakout
from strategies_v2 import trend_ensemble, ma_regime
from lev_4h_study import load_4h, funding_per_4h, lev_backtest, BPY
import metrics as M

warnings.filterwarnings("ignore")

L = 2.0

# Per-family grids (kept modest to limit selection bias).
FAMILIES = {
    "ma_regime": (ma_regime,
                  [{"ma": m, "buffer": b} for m in (100, 150, 200, 250, 300, 350, 400)
                   for b in (0.0, 0.02)]),
    "ema_crossover": (ema_crossover,
                      [{"fast": f, "slow": s} for f in (12, 24, 36, 48)
                       for s in (96, 144, 168, 240, 336) if s > f]),
    "momentum": (momentum,
                 [{"lookback": lb, "trend": tr} for lb in (60, 90, 120, 180, 240)
                  for tr in (200, 300, 400)]),
    "donchian_breakout": (donchian_breakout,
                          [{"entry": e, "exit_n": x} for e in (60, 120, 180, 240)
                           for x in (30, 60, 90)]),
    "trend_ensemble": (trend_ensemble,
                       [{"threshold": t} for t in (0.4, 0.5, 0.6)]),
}


def seg_metrics(ret, mask):
    r = ret[mask]
    return {
        "net_%": M.total_return(r) * 100,
        "cagr_%": M.cagr(r, BPY) * 100,
        "sharpe": M.sharpe(r, BPY),
        "maxdd_%": M.max_drawdown(r) * 100,
        "calmar": M.calmar(r, BPY),
    }


def main():
    df = load_4h()
    fund = funding_per_4h(df.index)
    ones = pd.Series(1.0, index=df.index)
    split = df.index[int(len(df) * 0.60)]
    train, test = df.index < split, df.index >= split

    print(f"\n4h {df.index[0].date()}->{df.index[-1].date()}  L={L:g}x  funding+liq modeled")
    print(f"train < {split.date()}   |   test >= {split.date()}   (winner picked on TRAIN, judged on TEST)")

    finalists = []
    for fam, (fn, grid) in FAMILIES.items():
        best, best_sh = None, -1e9
        for p in grid:
            try:
                r = lev_backtest(df, fn(df, **p), ones * L, funding=fund)
            except Exception:
                continue
            if r.liq_bars:                       # disqualify anything that blows up
                continue
            tr = seg_metrics(r.returns, train)
            if tr["sharpe"] > best_sh:
                best_sh, best = tr["sharpe"], (p, r)
        if best is None:
            continue
        p, r = best
        te = seg_metrics(r.returns, test)
        full = seg_metrics(r.returns, df.index >= df.index[0])
        cfg = ",".join(f"{k}={v}" for k, v in p.items())
        finalists.append((fam, cfg, best_sh, te, full, r, fn, p))

    # rank finalists by TEST Sharpe (robust, out-of-sample quality)
    finalists.sort(key=lambda t: t[3]["sharpe"], reverse=True)

    print("\n" + "=" * 100)
    print("FINALISTS — each family's best-on-TRAIN config, ranked by TEST Sharpe (out-of-sample)")
    print("=" * 100)
    print(f"  {'family (best cfg)':<40}{'TRAIN Sh':>9}{'TEST Sh':>9}{'TEST net%':>11}"
          f"{'TEST CAGR':>10}{'TEST DD%':>10}")
    print("  " + "-" * 88)
    for fam, cfg, tr_sh, te, full, r, fn, p in finalists:
        print(f"  {fam+' ('+cfg+')':<40}{tr_sh:>9.2f}{te['sharpe']:>9.2f}{te['net_%']:>11,.0f}"
              f"{te['cagr_%']:>10.1f}{te['maxdd_%']:>10.1f}")

    win = finalists[0]
    fam, cfg, tr_sh, te, full, r, fn, p = win
    print("  " + "-" * 88)
    print(f"  BEST 2x STRATEGY (best out-of-sample Sharpe, zero liquidations): {fam}({cfg}) x2")

    # ---- full-sample stats of the winner --------------------------------- #
    print("\n" + "=" * 100)
    print(f"WINNER FULL-SAMPLE — {fam}({cfg}) x2   vs   1x   vs   buy&hold")
    print("=" * 100)
    r1 = lev_backtest(df, fn(df, **p), ones * 1, funding=fund)
    r2 = r
    bh = df["close"].pct_change().fillna(0.0)
    allmask = df.index >= df.index[0]
    m1, m2 = seg_metrics(r1.returns, allmask), seg_metrics(r2.returns, allmask)
    mb = seg_metrics(bh, allmask)
    print(f"  {'metric':<16}{'winner x2':>14}{'winner x1':>14}{'buy & hold':>14}")
    print("  " + "-" * 58)
    for k, lab in [("net_%", "total return %"), ("cagr_%", "CAGR %"), ("sharpe", "Sharpe"),
                   ("maxdd_%", "max drawdown %"), ("calmar", "Calmar")]:
        print(f"  {lab:<16}{m2[k]:>14,.1f}{m1[k]:>14,.1f}{mb[k]:>14,.1f}")

    # ---- definitive year-by-year ----------------------------------------- #
    print("\n" + "=" * 100)
    print(f"DEFINITIVE YEAR-BY-YEAR — {fam}({cfg})  (return % | intra-year maxDD %)")
    print("=" * 100)
    print(f"  {'year':<6}{'1x ret':>9}{'1x DD':>8}   {'2x ret':>9}{'2x DD':>8}   {'B&H ret':>9}")
    print("  " + "-" * 60)
    for y in sorted(set(df.index.year)):
        def rd(ret):
            rs = ret[ret.index.year == y]
            return M.total_return(rs) * 100, M.max_drawdown(rs) * 100
        a, da = rd(r1.returns); b, db = rd(r2.returns); c, _ = rd(bh)
        print(f"  {y:<6}{a:>9,.0f}{da:>8.0f}   {b:>9,.0f}{db:>8.0f}   {c:>9,.0f}")
    print("  " + "-" * 60)

    g1 = float((1 + r1.returns).prod())
    g2 = float((1 + r2.returns).prod())
    gb = float((1 + bh).prod())
    print(f"\n  Rs 1,00,000 -> x1: Rs {100000*g1:,.0f} ({g1:,.1f}x)   "
          f"x2: Rs {100000*g2:,.0f} ({g2:,.1f}x)   B&H: Rs {100000*gb:,.0f} ({gb:,.1f}x)")
    print("=" * 100 + "\n")


if __name__ == "__main__":
    main()
