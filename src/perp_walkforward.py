"""
FULL research pipeline on REAL Binance perpetual data:
  * backtest (regression)  — in-sample fit of the whole grid
  * WALK-FORWARD (forward test) — rolling train->test, params chosen ONLY on past
    data, stitched into ONE honest out-of-sample equity curve
  * optimize — report the params the walk-forward actually keeps picking

Realism carried throughout:
  * PnL & execution on the PERP close (real tradeable price, not spot)
  * LIQUIDATION checked against MARK-price high/low (what Binance liquidates on)
  * real 8h FUNDING drag on the levered notional
  * taker fee + slippage on turnover of the levered notional
  * maintenance margin 0.5%; a config that liquidates in a window is disqualified

Leverage: default 2x (Sharpe is leverage-invariant, so we optimize the BASE by
Sharpe, then apply the chosen 2x). Pass a different L on the CLI.

Usage:  python src/perp_walkforward.py         # 2x
        python src/perp_walkforward.py 3        # 3x
"""
from __future__ import annotations

import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategies import momentum, ema_crossover, donchian_breakout
from strategies_v2 import trend_ensemble, ma_regime
import metrics as M

warnings.filterwarnings("ignore")

DATA = Path(__file__).resolve().parent.parent / "data"
FEE, SLIP, MM = 0.0004, 0.0003, 0.005     # perp taker 0.04% + 3bps slip (futures are cheaper than spot)
BPY = 6 * 365


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def load() -> pd.DataFrame:
    perp = pd.read_parquet(next(DATA.glob("BTCUSDT_PERP_4h_*.parquet")))
    mark = pd.read_parquet(next(DATA.glob("BTCUSDT_MARK_4h_*.parquet")))
    fund = pd.read_parquet(DATA / "BTCUSDT_funding.parquet")["fundingRate"]
    df = perp.join(mark, how="left")
    # before mark history starts, use perp extremes as the liq reference
    df["mark_high"] = df["mark_high"].fillna(df["high"])
    df["mark_low"] = df["mark_low"].fillna(df["low"])
    fund = fund[~fund.index.duplicated()].sort_index()
    # Binance occasionally timestamps a funding event a few milliseconds after
    # 00/08/16 UTC. Map it into its containing 4h bar instead of silently
    # dropping it through an exact-index reindex.
    fund.index = fund.index.floor("4h")
    fund = fund.groupby(level=0).sum()
    df["funding"] = fund.reindex(df.index).fillna(0.0)
    return df.dropna(subset=["close"])


# --------------------------------------------------------------------------- #
# Leverage engine (perp close PnL, mark-price liquidation, funding)
# --------------------------------------------------------------------------- #
@dataclass
class Res:
    returns: pd.Series
    position: pd.Series
    liq: int
    first_liq: pd.Timestamp | None


def run(df, target_pos, L, fee=FEE, slip=SLIP, mm=MM) -> Res:
    close = df["close"]
    bar_ret = close.pct_change().fillna(0.0)
    lev = (target_pos.reindex(df.index).ffill().fillna(0.0) * L)
    ex = lev.shift(1).fillna(0.0)
    dpos = ex.diff().abs().fillna(ex.abs())
    cost = dpos * (fee + slip)
    funding_cost = ex.abs() * df["funding"]
    ret = ex * bar_ret - cost - funding_cost

    # liquidation vs MARK low (long); threshold adverse move = 1/L - mm
    prev = close.shift(1)
    adverse = ((prev - df["mark_low"]) / prev).fillna(0.0)
    Leff = ex.abs()
    thr = np.where(Leff > 1e-9, 1.0 / Leff - mm, np.inf)
    liq_mask = (adverse.values >= thr) & (Leff.values > 1.0 + 1e-9)
    idx = df.index[liq_mask]
    first = idx[0] if len(idx) else None
    if first is not None:
        ret = ret.copy()
        ret.loc[first] = -1.0
        ret.loc[ret.index > first] = 0.0
    return Res(ret, ex, int(liq_mask.sum()), first)


def stats(ret, mask=None):
    r = ret if mask is None else ret[mask]
    return {"net_%": M.total_return(r) * 100, "cagr_%": M.cagr(r, BPY) * 100,
            "sharpe": M.sharpe(r, BPY), "maxdd_%": M.max_drawdown(r) * 100,
            "calmar": M.calmar(r, BPY)}


# --------------------------------------------------------------------------- #
# Search space
# --------------------------------------------------------------------------- #
def candidates():
    out = []
    for m in (100, 150, 200, 250, 300, 350, 400):
        out.append((f"ma_regime(ma={m})", lambda d, m=m: ma_regime(d, ma=m)))
    for f in (12, 24, 36, 48):
        for s in (96, 120, 144, 168, 240):
            if s > f:
                out.append((f"ema({f},{s})", lambda d, f=f, s=s: ema_crossover(d, fast=f, slow=s)))
    for lb in (60, 90, 120, 180):
        for tr in (200, 300):
            out.append((f"mom({lb},{tr})", lambda d, lb=lb, tr=tr: momentum(d, lookback=lb, trend=tr)))
    for e in (60, 120, 180):
        for x in (30, 60, 90):
            out.append((f"donch({e},{x})", lambda d, e=e, x=x: donchian_breakout(d, entry=e, exit_n=x)))
    for t in (0.4, 0.5, 0.6):
        out.append((f"ens({t})", lambda d, t=t: trend_ensemble(d, threshold=t)))
    return out


def pick_best(df, mask, L):
    """Best config by Sharpe on df[mask]; disqualify any that liquidate in-window."""
    best, best_key = None, (-1e9, -1e9)
    for name, fn in candidates():
        try:
            r = run(df, fn(df), L)
        except Exception:
            continue
        if int((r.position[mask].abs() > 1).any()) and _liq_in(df, r, mask):
            continue
        s = stats(r.returns, mask)
        key = (round(s["sharpe"], 4), round(s["calmar"], 4))
        if key > best_key:
            best_key, best = key, (name, fn, s)
    return best


def _liq_in(df, r, mask):
    prev = df["close"].shift(1)
    adverse = ((prev - df["mark_low"]) / prev)
    Leff = r.position.abs()
    thr = 1.0 / Leff.replace(0, np.nan) - MM
    liq = (adverse >= thr) & (Leff > 1.0)
    return bool(liq[mask].any())


# --------------------------------------------------------------------------- #
def main():
    L = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].replace('.', '').isdigit() else 2.0
    df = load()
    print(f"\nREAL Binance PERP {df.index[0].date()}->{df.index[-1].date()}  {len(df)} 4h bars")
    print(f"L={L:g}x  perp fee {FEE*100:.2f}%+{SLIP*100:.2f}% slip, mark-price liq (mm {MM*100:.1f}%), real funding\n")

    # ---- 1. REGRESSION (full in-sample) — top 8 configs ------------------ #
    full = df.index >= df.index[0]
    scored = []
    for name, fn in candidates():
        r = run(df, fn(df), L)
        s = stats(r.returns, full)
        scored.append((name, s, r.liq))
    scored.sort(key=lambda t: t[1]["sharpe"], reverse=True)
    print("=" * 88)
    print("1) REGRESSION / BACKTEST — full-sample, ranked by Sharpe (top 8)")
    print("=" * 88)
    print(f"  {'config':<20}{'net_%':>12}{'CAGR%':>8}{'Sharpe':>8}{'maxDD%':>9}{'Calmar':>8}{'liq':>5}")
    for name, s, liq in scored[:8]:
        print(f"  {name:<20}{s['net_%']:>12,.0f}{s['cagr_%']:>8.1f}{s['sharpe']:>8.2f}"
              f"{s['maxdd_%']:>9.1f}{s['calmar']:>8.2f}{liq:>5}")

    # ---- 2. WALK-FORWARD (true forward test) ----------------------------- #
    train_bars = int(1.5 * BPY)   # 18 months
    test_bars = int(0.5 * BPY)    # 6 months
    start = train_bars
    oos_ret = pd.Series(0.0, index=df.index)
    oos_ret[:] = np.nan
    folds = []
    i = start
    while i + 1 < len(df):
        tr_mask = pd.Series(False, index=df.index)
        te_mask = pd.Series(False, index=df.index)
        tr_lo, tr_hi = i - train_bars, i
        te_lo, te_hi = i, min(i + test_bars, len(df))
        tr_mask.iloc[tr_lo:tr_hi] = True
        te_mask.iloc[te_lo:te_hi] = True
        best = pick_best(df, tr_mask, L)
        if best is not None:
            name, fn, tr_s = best
            r = run(df, fn(df), L)
            oos_ret.iloc[te_lo:te_hi] = r.returns.iloc[te_lo:te_hi].values
            te_s = stats(r.returns, te_mask)
            folds.append((df.index[te_lo].date(), df.index[te_hi - 1].date(), name,
                          tr_s["sharpe"], te_s))
        i += test_bars

    print("\n" + "=" * 88)
    print(f"2) WALK-FORWARD — 18mo train -> 6mo test, params chosen on PAST data only")
    print("=" * 88)
    print(f"  {'test window':<24}{'chosen config':<16}{'trSh':>6}{'teNet%':>9}{'teCAGR':>8}{'teSh':>7}{'teDD%':>8}")
    from collections import Counter
    picks = Counter()
    for lo, hi, name, trsh, te in folds:
        picks[name] += 1
        print(f"  {str(lo)+'->'+str(hi):<24}{name:<16}{trsh:>6.2f}{te['net_%']:>9,.0f}"
              f"{te['cagr_%']:>8.1f}{te['sharpe']:>7.2f}{te['maxdd_%']:>8.1f}")

    oos = oos_ret.dropna()
    os = stats(oos, oos.index >= oos.index[0])
    print("  " + "-" * 84)
    print(f"  STITCHED OUT-OF-SAMPLE ({oos.index[0].date()}->{oos.index[-1].date()}): "
          f"net {os['net_%']:,.0f}%  CAGR {os['cagr_%']:.1f}%  Sharpe {os['sharpe']:.2f}  "
          f"maxDD {os['maxdd_%']:.1f}%  Calmar {os['calmar']:.2f}")

    # ---- 3. OPTIMIZE — what the walk-forward keeps choosing -------------- #
    print("\n" + "=" * 88)
    print("3) OPTIMIZE — configs the walk-forward selected most (robust choices)")
    print("=" * 88)
    for name, n in picks.most_common(6):
        print(f"  {name:<20} chosen in {n}/{len(folds)} folds")
    robust = picks.most_common(1)[0][0]
    print(f"\n  Most robust across time: {robust}")

    # ---- OOS year-by-year of the walk-forward --------------------------- #
    print("\n" + "=" * 88)
    print("WALK-FORWARD out-of-sample YEAR-BY-YEAR (return % | intra-year maxDD %)")
    print("=" * 88)
    bh = df["close"].pct_change().fillna(0.0)
    print(f"  {'year':<6}{'WF ret':>10}{'WF DD':>8}   {'B&H ret':>10}")
    for y in sorted(set(oos.index.year)):
        rs = oos[oos.index.year == y]
        bhs = bh[(bh.index.year == y) & (bh.index >= oos.index[0])]
        print(f"  {y:<6}{M.total_return(rs)*100:>10,.0f}{M.max_drawdown(rs)*100:>8.0f}   "
              f"{M.total_return(bhs)*100:>10,.0f}")
    g = float((1 + oos).prod())
    print(f"\n  Rs 1,00,000 -> walk-forward OOS: Rs {100000*g:,.0f} ({g:,.1f}x)  "
          f"[{oos.index[0].date()}->{oos.index[-1].date()}, pre-tax]")
    print("=" * 88 + "\n")


if __name__ == "__main__":
    main()
