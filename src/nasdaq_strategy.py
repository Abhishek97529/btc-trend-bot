"""
Leveraged swing/positional strategy for US-index perpetual futures (CoinDCX
"global futures": NASDAQ-100 / S&P-500). Goal: beat buy-and-hold monthly & yearly.

Data: daily OHLC for ^NDX (1985->now, includes the -83% dot-com crash & 2008) and
^GSPC, from Yahoo. 40 years is deliberate: it stress-tests leverage where it is
LETHAL, so we don't design something that only survives the post-2009 bull.

Perp modeling:
  - Positions are leverage in units of equity (1.0 = fully long, 0 = flat).
  - FUNDING/FINANCING charged on leverage above 1x at FUND_ANNUAL (a perp long pays
    carry). Charged per bar on max(lev-1, 0). Buy&hold = constant 1x, no financing.
  - Costs COST bps/side on turnover (perp taker+slippage).
  - LIQUIDATION check: a leveraged long on a perp dies if the intraday drop exceeds
    the maintenance buffer for its leverage. We model it on the daily low so the
    backtest can't ignore a wipeout (crypto-exchange perps liquidate hard).

Strategies:
  BH        buy & hold, 1x.
  TREND     regime (close>200SMA) AND fast (close>50SMA); when on, size by vol-target
            lev = clip(target_vol/realized_vol, 0, MAX_LEV); else FLAT. Sits out bears.
  DIPLEV    always 1x + overlay tranche when oversold (below lower Bollinger) AND in
            an uptrend (>200SMA). The Nifty winner, retested on US indices.
  EQRISK    regime long/flat levered by a constant to match B&H volatility.

No lookahead: signal on close[t], position held t->t+1.

Usage:  python src/nasdaq_strategy.py [ndx|spx]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "markets" / "us"
REPORT_DIR = ROOT / "reports" / "legacy-markets" / "us"

BPY = 252
COST = 0.0004          # 4 bps/side
FUND_ANNUAL = 0.08     # 8%/yr financing on leverage above 1x (perp carry); stressed later
FUND_D = FUND_ANNUAL / BPY
MAINT = 0.005          # 0.5% maintenance margin (liquidation buffer)
MAX_LEV = 2.0
VOL_TARGET = 0.20      # US indices run hotter than Nifty; 20% target
SLOW, FAST, VOL_LB, BB_N, BB_K = 200, 50, 20, 20, 2.0


def load(which: str) -> pd.DataFrame:
    f = DATA_DIR / ("data_ndx.csv" if which == "ndx" else "data_spx.csv")
    return pd.read_csv(f, parse_dates=["date"]).set_index("date").sort_index()


def lev_returns(pos, ret, low_ret):
    """Net daily return for a leveraged long perp with funding + liquidation.

    pos = signed leverage held over the bar (already lagged). A long with leverage L
    is liquidated when the intraday move (low_ret) <= -(1-MAINT)/L, in which case the
    bar return is -1 (wiped) and the position is forced flat that day.
    """
    turnover = pos.diff().abs().fillna(pos.abs())
    borrow = (pos - 1.0).clip(lower=0.0)
    # liquidation: adverse intraday move beyond the margin buffer
    with np.errstate(divide="ignore"):
        liq_thresh = np.where(pos > 0, -(1 - MAINT) / pos.replace(0, np.nan), -np.inf)
    liq = (low_ret.values <= liq_thresh) & (pos.values > 0)
    gross = pos * ret
    net = gross - turnover * COST - borrow * FUND_D
    net = net.where(~liq, -1.0)          # wiped out on liquidation days
    return net.fillna(0.0), int(liq.sum())


def build(which: str):
    df = load(which)
    close, high, low = df["close"], df["high"], df["low"]
    ret = close.pct_change().fillna(0.0)
    low_ret = (low / close.shift(1) - 1).fillna(0.0)     # worst intraday move vs prior close

    sma_s = close.rolling(SLOW).mean()
    sma_f = close.rolling(FAST).mean()
    rvol = ret.rolling(VOL_LB).std() * np.sqrt(BPY)

    regime = (close > sma_s).astype(float)
    both_up = ((close > sma_s) & (close > sma_f)).astype(float)
    vt = (VOL_TARGET / rvol).clip(0.0, MAX_LEV)

    pos_bh = pd.Series(1.0, index=close.index)
    pos_trend = (both_up * vt).shift(1).fillna(0.0)

    # dip-leverage (gated)
    mid = close.rolling(BB_N).mean(); sd = close.rolling(BB_N).std()
    lower = mid - BB_K * sd
    dip = np.zeros(len(close)); inpos = False
    e = (close < lower).values; x = (close >= mid).values
    for i in range(len(close)):
        inpos = (inpos and not x[i]) or (not inpos and e[i])
        dip[i] = 1.0 if inpos else 0.0
    dip = pd.Series(dip, index=close.index)
    pos_dip = (1.0 + 1.0 * dip * regime).shift(1).fillna(1.0)

    # equal-risk: regime long/flat scaled to BH vol
    base_reg = regime.shift(1).fillna(0.0)
    r_reg, _ = lev_returns(base_reg, ret, low_ret)
    r_bh, _ = lev_returns(pos_bh, ret, low_ret)
    scale = min(MAX_LEV, M.ann_vol(r_bh, BPY) / max(M.ann_vol(r_reg, BPY), 1e-9))
    pos_eqrisk = (base_reg * scale).clip(0.0, MAX_LEV)

    # SWING: gate on 200SMA (sidestep bears), vol-target the base exposure with a
    # floor so it stays invested to capture bulls, and add a dip boost. Capped MAX_LEV.
    vt_swing = (VOL_TARGET / rvol).clip(0.75, MAX_LEV)     # floor 0.75x when in uptrend
    swing_lev = (regime * (vt_swing + 1.0 * dip)).clip(0.0, MAX_LEV)
    pos_swing = swing_lev.shift(1).fillna(0.0)

    specs = [("BH", pos_bh), ("TREND", pos_trend), ("DIPLEV", pos_dip),
             ("EQRISK", pos_eqrisk), ("SWING", pos_swing)]
    rets, liqs = {}, {}
    for name, pos in specs:
        r, nliq = lev_returns(pos, ret, low_ret)
        rets[name] = r; liqs[name] = nliq
    build.pos = {n: p for n, p in specs}
    build.liqs = liqs; build.scale = scale; build.index = close.index
    return rets


def clip_win(r, years):
    if years is None:
        return r
    return r[r.index >= r.index.max() - pd.Timedelta(days=int(years * 365.25))]


def metric_row(r):
    return {"totRet%": M.total_return(r) * 100, "CAGR%": M.cagr(r, BPY) * 100,
            "vol%": M.ann_vol(r, BPY) * 100, "sharpe": M.sharpe(r, BPY),
            "maxDD%": M.max_drawdown(r) * 100, "calmar": M.calmar(r, BPY)}


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "ndx"
    name = "NASDAQ-100" if which == "ndx" else "S&P 500"
    rets = build(which)
    idx = build.index
    order = ["BH", "TREND", "DIPLEV", "EQRISK", "SWING"]

    print("=" * 88)
    print(f"{name} PERP-FUTURES SWING STRATEGY  ({idx.min().date()} -> {idx.max().date()}, "
          f"{len(idx)} bars, {(idx.max()-idx.min()).days/365.25:.0f}y)")
    print(f"costs {COST*1e4:.0f}bp/side | funding {FUND_ANNUAL*100:.0f}%/yr on lev>1 | "
          f"maxlev {MAX_LEV}x | liquidation modeled")
    print(f"EQRISK leverage scale = {build.scale:.2f}x | liquidations: "
          + ", ".join(f"{k}={v}" for k, v in build.liqs.items()))
    print("=" * 88)

    for wname, yrs in [("full", None), ("10-year", 10.0), ("5-year", 5.0), ("3-year", 3.0)]:
        print(f"\n--- {wname} ---")
        tbl = pd.DataFrame({n: metric_row(clip_win(rets[n], yrs)) for n in order}).T.round(2)
        bh_r, bh_s = tbl.loc["BH", "totRet%"], tbl.loc["BH", "sharpe"]
        tbl["beatRet"] = np.where(tbl["totRet%"] > bh_r, "Y", "-")
        tbl["beatShrp"] = np.where(tbl["sharpe"] > bh_s, "Y", "-")
        print(tbl.to_string())

    # yearly
    print("\n--- YEAR-BY-YEAR TOTAL RETURN (%) ---")
    years = sorted({d.year for d in idx})
    rows = []
    for y in years:
        row = {"year": y}
        for n in order:
            row[n] = M.total_return(rets[n][rets[n].index.year == y]) * 100
        rows.append(row)
    yt = pd.DataFrame(rows).set_index("year").round(1)
    print(yt.to_string())
    for n in ["TREND", "DIPLEV", "EQRISK", "SWING"]:
        print(f"{n}: beat B&H in {(yt[n] > yt['BH']).sum()}/{len(yt)} years")

    # monthly consistency
    print("\n--- MONTHLY CONSISTENCY (full period) ---")
    for n in order:
        m = rets[n].groupby([rets[n].index.year, rets[n].index.month]).apply(lambda x: M.total_return(x))
        print(f"  {n:<8} {100*(m>0).mean():4.0f}% months positive | avg {m.mean()*100:5.2f}% | "
              f"worst {m.min()*100:6.1f}% | best {m.max()*100:5.1f}%")

    # save equity for plot
    eq = pd.DataFrame({n: (1 + rets[n]).cumprod() for n in order}, index=idx)
    out = REPORT_DIR / f"nasdaq_equity_{which}.csv"
    eq.to_csv(out)
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
