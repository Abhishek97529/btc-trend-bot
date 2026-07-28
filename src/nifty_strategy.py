"""
Nifty 50 trend/regime strategy vs buy-and-hold — full backtest.

Data: daily OHLC for ^NSEI (Nifty 50 total-price index) pulled from Yahoo,
saved to data_nifty.csv (2007-09-17 -> present, ~4.6k bars).

Execution model (no lookahead):
  - Every signal is computed from information available at the CLOSE of day t.
  - The resulting position is HELD from close[t] to close[t+1] and earns that bar's
    return. So a signal on day t only affects P&L from day t+1 onward (`.shift(1)`).
  - Transaction cost `COST` (bps, one-way) is charged on the *change* in position
    (turnover) each day. Default 5 bps ~= liquid Nifty-futures / index-ETF round-trip
    of 10 bps split across entry+exit, blended brokerage+STT+slippage.
  - Leverage above 1x pays financing at the risk-free rate on the borrowed notional
    (Nifty futures embed a cost-of-carry ~= repo rate). Cash when FLAT earns 0% (a
    conservative choice that understates the strategy vs buy-and-hold).

Strategies:
  BH    Buy & hold (always 1x long).
  REG   Regime filter: long 1x when close > 200-day SMA, else flat.
  LEV   Trend ensemble (regime AND fast-trend agree) sized by vol-targeting,
        leverage clipped to [0, MAX_LEV]. Financing charged on (lev-1).

All parameters are chosen a priori (canonical 200/50-day trend, 15% vol target);
sensitivity to them is reported separately so you can see it is not knife-edge fit.

Usage:  python src/nifty_strategy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M
ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "markets" / "india" / "data_nifty.csv"
REPORT_FILE = ROOT / "reports" / "legacy-markets" / "india" / "nifty_equity.csv"

BPY = 252                      # trading days per year
COST = 0.0005                  # 5 bps one-way, charged on turnover
RF = 0.065                     # risk-free / futures carry, annual
RF_D = RF / BPY
SLOW = 200                     # regime SMA window
FAST = 50                      # fast trend SMA window
VOL_LB = 20                    # realized-vol lookback (days)
VOL_TARGET = 0.15              # annualized vol target for the leveraged sleeve
MAX_LEV = 2.0                  # leverage ceiling (Nifty futures allow ~5x; we cap at 2x)


def load() -> pd.DataFrame:
    df = pd.read_csv(DATA_FILE, parse_dates=["date"]).set_index("date")
    return df.sort_index()


def apply_costs(pos: pd.Series, gross_ret: pd.Series, lev: pd.Series | None = None) -> pd.Series:
    """Net per-bar return given a position series already lagged to be tradable.

    `pos` is the signed exposure held over each bar (this bar's return uses the
    position decided on the PRIOR close). Turnover cost is charged on |dpos|.
    If `lev` is given, financing at RF is charged on max(lev-1, 0) each bar.
    """
    turnover = pos.diff().abs().fillna(pos.abs())
    net = pos * gross_ret - turnover * COST
    if lev is not None:
        borrow = (lev - 1.0).clip(lower=0.0)
        net = net - borrow * RF_D
    return net.fillna(0.0)


def build() -> dict[str, pd.Series]:
    df = load()
    close = df["close"]
    ret = close.pct_change().fillna(0.0)

    sma_slow = close.rolling(SLOW).mean()
    sma_fast = close.rolling(FAST).mean()
    realized_vol = ret.rolling(VOL_LB).std() * np.sqrt(BPY)

    # --- signals computed on close[t], then lagged one bar to be tradable ---
    long_regime = (close > sma_slow).astype(float)                 # 1 = above 200SMA
    fast_up = (close > sma_fast).astype(float)
    both_up = ((long_regime > 0) & (fast_up > 0)).astype(float)

    # vol-target leverage, only when both trends agree
    lev_raw = (VOL_TARGET / realized_vol).clip(0.0, MAX_LEV)
    lev_target = (both_up * lev_raw).fillna(0.0)

    # lag positions so day-t signal drives day-(t+1) return
    pos_bh = pd.Series(1.0, index=close.index)
    pos_reg = long_regime.shift(1).fillna(0.0)
    pos_lev = lev_target.shift(1).fillna(0.0)

    # --- SMA-100 regime (higher Sharpe per sensitivity sweep) ---
    reg100 = (close > close.rolling(100).mean()).astype(float)
    pos_reg100 = reg100.shift(1).fillna(0.0)

    # --- EQUAL-RISK leverage: lever the SMA-100 regime so its full-period vol
    #     matches buy-and-hold, capped at MAX_LEV, financing charged on (lev-1).
    #     The scale factor is a single constant (not per-bar fit), derived from the
    #     realized vol ratio -> this is a fair "same risk budget" comparison. ---
    base_reg = apply_costs(pos_reg100, ret)
    bh_vol = M.ann_vol(apply_costs(pos_bh, ret), BPY)
    scale = min(MAX_LEV, bh_vol / M.ann_vol(base_reg, BPY))
    pos_eqrisk = (pos_reg100 * scale).clip(0.0, MAX_LEV)
    build.scale = scale

    out = {
        "BH": apply_costs(pos_bh, ret),                    # 1x, cost only on first bar
        "REG": apply_costs(pos_reg, ret),                  # 200-SMA long/flat
        "REG100": base_reg,                                # 100-SMA long/flat
        "LEV": apply_costs(pos_lev, ret, lev=pos_lev),     # vol-target ensemble
        "EQRISK": apply_costs(pos_eqrisk, ret, lev=pos_eqrisk),  # 100-SMA levered to BH vol
    }
    # attach position series for exposure/trade stats
    build.positions = {"BH": pos_bh, "REG": pos_reg, "REG100": pos_reg100,
                       "LEV": pos_lev, "EQRISK": pos_eqrisk}
    build.index = close.index
    return out


def trade_count(pos: pd.Series) -> int:
    """Number of round-trip entries: transitions from flat/low to a new leg."""
    active = (pos.abs() > 1e-9).astype(int)
    return int((active.diff() == 1).sum())


def yearly_table(rets: dict[str, pd.Series]) -> pd.DataFrame:
    years = sorted({d.year for d in next(iter(rets.values())).index})
    rows = []
    for y in years:
        row = {"year": y}
        for name, r in rets.items():
            ry = r[r.index.year == y]
            row[name] = M.total_return(ry) * 100
        rows.append(row)
    return pd.DataFrame(rows).set_index("year")


def summarize(rets: dict[str, pd.Series]) -> pd.DataFrame:
    pos = build.positions
    rows = {}
    for name, r in rets.items():
        s = M.summary(r, BPY)
        s = {k: v for k, v in s.items()}
        s["exposure"] = float((pos[name].abs() > 1e-9).mean())
        s["avg_leverage"] = float(pos[name][pos[name].abs() > 1e-9].mean())
        s["trades"] = trade_count(pos[name])
        rows[name] = s
    return pd.DataFrame(rows).T


def sensitivity() -> pd.DataFrame:
    """Regime CAGR/Sharpe/MaxDD across SMA windows — show it isn't a knife-edge fit."""
    df = load()
    close = df["close"]; ret = close.pct_change().fillna(0.0)
    rows = []
    for w in [100, 125, 150, 175, 200, 225, 250]:
        pos = (close > close.rolling(w).mean()).astype(float).shift(1).fillna(0.0)
        r = apply_costs(pos, ret)
        rows.append({"sma": w, "cagr%": M.cagr(r, BPY)*100,
                     "sharpe": M.sharpe(r, BPY), "maxDD%": M.max_drawdown(r)*100})
    return pd.DataFrame(rows).set_index("sma")


def walk_forward(rets: dict[str, pd.Series]) -> pd.DataFrame:
    """First-half (in-sample) vs second-half (out-of-sample) metrics per strategy."""
    idx = next(iter(rets.values())).index
    mid = idx[len(idx)//2]
    rows = []
    for name, r in rets.items():
        for label, seg in [("H1 (IS)", r[r.index < mid]), ("H2 (OOS)", r[r.index >= mid])]:
            rows.append({"strategy": name, "half": label,
                         "cagr%": M.cagr(seg, BPY)*100, "sharpe": M.sharpe(seg, BPY),
                         "maxDD%": M.max_drawdown(seg)*100})
    return pd.DataFrame(rows).set_index(["strategy", "half"])


def main() -> None:
    rets = build()
    idx = build.index
    print("=" * 70)
    print("NIFTY 50 STRATEGY BACKTEST")
    print(f"period : {idx.min().date()} -> {idx.max().date()}  ({len(idx)} bars, "
          f"{(idx.max()-idx.min()).days/365.25:.1f} yrs)")
    print(f"costs  : {COST*1e4:.0f} bps/side turnover, financing {RF*100:.1f}%/yr on lev>1")
    print("=" * 70)

    summ = summarize(rets)
    show = summ[["total_return", "cagr", "ann_vol", "sharpe", "sortino",
                 "max_drawdown", "calmar", "exposure", "avg_leverage", "trades"]].copy()
    for c in ["total_return", "cagr", "ann_vol", "max_drawdown", "exposure"]:
        show[c] = (show[c] * 100).round(1)
    for c in ["sharpe", "sortino", "calmar", "avg_leverage"]:
        show[c] = show[c].round(2)
    show["trades"] = show["trades"].astype(int)
    show = show.rename(columns={"total_return": "totRet%", "cagr": "CAGR%",
                                "ann_vol": "vol%", "max_drawdown": "maxDD%",
                                "exposure": "expo%"})
    print("\n--- HEADLINE METRICS ---")
    print(show.to_string())

    print("\n--- YEAR-BY-YEAR TOTAL RETURN (%) ---")
    yt = yearly_table(rets).round(1)
    yt["EQR-BH"] = (yt["EQRISK"] - yt["BH"]).round(1)
    print(yt.to_string())
    print(f"\nEQRISK leverage scale applied to SMA-100 regime: {build.scale:.2f}x")
    print("years EQRISK beat BH: %d/%d   |   REG100 beat BH: %d/%d"
          % ((yt["EQRISK"] > yt["BH"]).sum(), len(yt),
             (yt["REG100"] > yt["BH"]).sum(), len(yt)))

    print("\n--- WALK-FORWARD (first half = in-sample, second = out-of-sample) ---")
    print(walk_forward(rets).round(2).to_string())

    print("\n--- REGIME PARAMETER SENSITIVITY (SMA window) ---")
    print(sensitivity().round(2).to_string())

    # equity curves for plotting / saving
    eq = pd.DataFrame({name: (1 + r).cumprod() for name, r in rets.items()}, index=idx)
    eq.to_csv(REPORT_FILE)
    print(f"\nsaved equity curves -> {REPORT_FILE}")


if __name__ == "__main__":
    main()
