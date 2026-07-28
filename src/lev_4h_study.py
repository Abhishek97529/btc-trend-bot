"""
LEVERAGED 4h BTC strategy design — done HONESTLY.

Most "10x on 4h!" backtests are fantasy because they ignore the two things that
actually kill leveraged accounts:
    1. FUNDING  — perps charge funding (~11%/yr drag on longs here, real 8h data).
    2. LIQUIDATION — one intrabar spike past your maintenance margin = account → 0,
       no matter how good the strategy looked on close-to-close returns.

This engine models BOTH. A config that liquidates even ONCE is marked RUINED
(equity → 0 from that bar), because with isolated margin on your whole stack, it is.

Leverage designs tested (all trend-gated — you only lever when the trend is UP):
  A. momentum(120,300) binary * L        (constant leverage when in-trend)
  B. trend_ensemble exposure * L         (lever the agreement fraction)
  C. VOL-TARGETED leverage               (lever = target_vol / realized_vol, capped)
        -> the smart one: auto-DELEVERS in high vol, which is exactly when
           liquidation happens. This is how pros run leverage.

Maintenance margin assumed 0.5% (Binance BTC low-tier). Liquidation when an
intrabar adverse move >= (1/L - mm). Funding charged on |position| each 8h.

Usage:  python src/lev_4h_study.py
"""
from __future__ import annotations

import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategies import momentum
from strategies_v2 import trend_ensemble, _trend_votes
from indicators import rolling_vol
import metrics as M

warnings.filterwarnings("ignore")

DATA = Path(__file__).resolve().parent.parent / "data"
DATA_1H = DATA / "BTCUSDT_1h_2019-01-01_2026-07-23.parquet"
FUNDING = DATA / "BTCUSDT_funding.parquet"
FEE, SLIP = 0.001, 0.0005
BPY = 6 * 365                     # 4h bars/yr
MM = 0.005                        # maintenance margin (0.5%)


def load_4h() -> pd.DataFrame:
    df = pd.read_parquet(DATA_1H)
    df = df[~df.index.duplicated()].sort_index()
    o = df["open"].resample("4h").first()
    h = df["high"].resample("4h").max()
    l = df["low"].resample("4h").min()
    c = df["close"].resample("4h").last()
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()


def funding_per_4h(index: pd.DatetimeIndex) -> pd.Series:
    """Real 8h funding reindexed onto 4h bars (0 on non-funding bars, 0 before data)."""
    f = pd.read_parquet(FUNDING)["fundingRate"]
    f = f[~f.index.duplicated()].sort_index()
    # snap each funding stamp to the 4h bar that contains it
    fr = f.reindex(index, method=None).fillna(0.0)
    # funding stamps at 00/08/16 fall exactly on 4h bars (00/08/16 are 4h grid points)
    return fr


@dataclass
class LevResult:
    equity: pd.Series
    returns: pd.Series
    position: pd.Series
    trades: int
    liq_bars: int
    first_liq: pd.Timestamp | None


def lev_backtest(df, target_pos, L_series, fee=FEE, slip=SLIP, funding=None,
                 mm=MM, allow_short=False) -> LevResult:
    """target_pos in [0,1] (or [-1,1]); actual leverage each bar = target_pos * L_series.
    Executes on next bar (shift 1). Models funding + intrabar liquidation."""
    close = df["close"]
    bar_ret = close.pct_change().fillna(0.0)

    lo = -np.inf if allow_short else 0.0
    lev = (target_pos.reindex(df.index).ffill().fillna(0.0)
           * L_series.reindex(df.index).ffill().fillna(0.0))
    executed = lev.shift(1).fillna(0.0)

    # costs on turnover of the LEVERED notional
    dpos = executed.diff().abs().fillna(executed.abs())
    cost = dpos * (fee + slip)

    # funding on |notional| — funding rate applies to the bar it lands on
    fund = pd.Series(0.0, index=df.index) if funding is None else funding.reindex(df.index).fillna(0.0)
    funding_cost = executed.abs() * fund     # long pays positive funding

    strat_ret = executed * bar_ret - cost - funding_cost

    # ---- intrabar liquidation overlay ------------------------------------ #
    prev_close = close.shift(1)
    if allow_short:
        # short liquidates on an up-spike; long on a down-spike
        adverse_long = (prev_close - df["low"]) / prev_close
        adverse_short = (df["high"] - prev_close) / prev_close
        adverse = np.where(executed >= 0, adverse_long, adverse_short)
        adverse = pd.Series(adverse, index=df.index).fillna(0.0)
    else:
        adverse = ((prev_close - df["low"]) / prev_close).fillna(0.0)

    Leff = executed.abs()
    thresh = np.where(Leff > 1e-9, 1.0 / Leff - mm, np.inf)
    liq_mask = (adverse.values >= thresh) & (Leff.values > 1.0 + 1e-9)  # only lev>1 can liq
    liq_idx = df.index[liq_mask]
    first_liq = liq_idx[0] if len(liq_idx) else None

    if first_liq is not None:
        # account wiped at first liquidation; equity 0 thereafter
        strat_ret = strat_ret.copy()
        strat_ret.loc[first_liq] = -1.0
        strat_ret.loc[strat_ret.index > first_liq] = 0.0

    equity = (1.0 + strat_ret).cumprod()
    trades = int((executed.diff().fillna(0) != 0).sum())
    return LevResult(equity, strat_ret, executed, trades, int(liq_mask.sum()), first_liq)


def summ(r: LevResult) -> dict:
    ret = r.returns
    expo = float((r.position.abs().values > 1e-9).mean()) * 100
    avg_lev = float(r.position.abs()[r.position.abs() > 1e-9].mean() or 0)
    return {
        "net_%": M.total_return(ret) * 100,
        "cagr_%": M.cagr(ret, BPY) * 100,
        "sharpe": M.sharpe(ret, BPY),
        "maxdd_%": M.max_drawdown(ret) * 100,
        "calmar": M.calmar(ret, BPY),
        "avg_lev": avg_lev,
        "expo_%": expo,
        "trades": r.trades,
        "liq": r.liq_bars,
        "first_liq": r.first_liq,
    }


def row(name, s):
    fl = s["first_liq"].date() if s["first_liq"] is not None else "-"
    tag = "  <== LIQUIDATED (RUIN)" if s["liq"] else ""
    return (f"  {name:<26}{s['net_%']:>12,.0f}{s['cagr_%']:>8.1f}{s['sharpe']:>7.2f}"
            f"{s['maxdd_%']:>8.1f}{s['avg_lev']:>7.2f}{s['expo_%']:>7.0f}{s['trades']:>7}"
            f"{s['liq']:>5}  {str(fl):>10}{tag}")


def main():
    df = load_4h()
    fund = funding_per_4h(df.index)
    print(f"\n4h bars: {len(df)}  {df.index[0].date()}->{df.index[-1].date()}  "
          f"mm={MM*100:.1f}%  funding=real 8h (mean {fund[fund!=0].mean()*100:.4f}%/8h)")
    print(f"costs {FEE*100:.2f}%+{SLIP*100:.3f}%/side, charged on LEVERED notional\n")

    hdr = (f"  {'strategy':<26}{'net_%':>12}{'CAGR%':>8}{'Sharpe':>7}{'maxDD%':>8}"
           f"{'avgLev':>7}{'in%':>7}{'trades':>7}{'liq':>5}{'firstLiq':>12}")
    print(hdr); print("  " + "-" * 110)

    ones = pd.Series(1.0, index=df.index)

    # baseline spot (L=1)
    mom = momentum(df, 120, 300)
    ens = trend_ensemble(df, 0.5)
    print(row("momentum L=1 (spot)", summ(lev_backtest(df, mom, ones, funding=fund))))
    print(row("ensemble L=1 (spot)", summ(lev_backtest(df, ens, ones, funding=fund))))
    print("  " + "-" * 110)

    # A. momentum * constant L
    for L in (1.5, 2.0, 3.0, 5.0):
        s = summ(lev_backtest(df, mom, ones * L, funding=fund))
        print(row(f"A momentum x{L:g}", s))
    print("  " + "-" * 110)

    # B. ensemble exposure * constant L
    for L in (1.5, 2.0, 3.0):
        s = summ(lev_backtest(df, ens, ones * L, funding=fund))
        print(row(f"B ensemble x{L:g}", s))
    print("  " + "-" * 110)

    # C. VOL-TARGETED leverage, trend-gated (the smart one)
    ret = df["close"].pct_change()
    realized = (rolling_vol(ret, 30) * np.sqrt(BPY)).replace(0, np.nan)
    gate = (_trend_votes(df).mean(axis=1) >= 0.5).astype(float)   # only lever in uptrend
    warm = df["close"].rolling(200, min_periods=200).mean().notna()
    for tv, cap in [(0.6, 2.0), (0.8, 2.0), (0.8, 3.0), (1.0, 3.0), (1.2, 4.0)]:
        lev = (tv / realized).clip(0.0, cap).fillna(0.0) * warm
        s = summ(lev_backtest(df, gate, lev, funding=fund))
        print(row(f"C voltgt {tv:g} cap{cap:g}", s))

    # ---- YEAR-BY-YEAR for momentum at L = 1 / 2 / 3 ---------------------- #
    print("\n" + "=" * 78)
    print("YEAR-BY-YEAR total return %  —  momentum(120,300) trend-gated leverage")
    print("=" * 78)
    print(f"  {'year':<6}{'L=1 (spot)':>13}{'L=2':>12}{'L=3':>12}{'B&H':>12}")
    print("  " + "-" * 55)
    rr = {L: lev_backtest(df, mom, ones * L, funding=fund).returns for L in (1, 2, 3)}
    bh_ret = df["close"].pct_change().fillna(0.0)
    for y in sorted(set(df.index.year)):
        def ytot(r):
            return M.total_return(r[r.index.year == y]) * 100
        print(f"  {y:<6}{ytot(rr[1]):>13,.0f}{ytot(rr[2]):>12,.0f}"
              f"{ytot(rr[3]):>12,.0f}{ytot(bh_ret):>12,.0f}")
    print("  " + "-" * 55)

    print("  " + "-" * 110)
    print("  Read: 'liq'>0 means an intrabar spike blew the account -> RUIN (equity 0).")
    print("  A leveraged config is only usable if liq==0 AND it beats spot on Sharpe/Calmar,")
    print("  not just raw %. Vol-targeting (C) delevers in chaos, so it should survive where")
    print("  constant leverage (A/B) gets liquidated.\n")


if __name__ == "__main__":
    main()
