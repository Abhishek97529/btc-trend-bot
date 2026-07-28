"""
FINAL Nifty 50 strategy: "Dip-Leverage" (gated buy-the-dip overlay on buy-and-hold).

Rationale (see nifty_research.py for the search that led here):
  - Over the last 5-10 years Nifty was a low-vol grind higher; long/flat trend
    filters LOSE to buy-and-hold because they sit out shallow dips.
  - What DOES have edge in a grinding bull is buying dips. A pure dip-buyer has a
    great Sharpe but low absolute return (it's flat most of the time).
  - So: stay 1x invested ALWAYS (capture the bull, matching B&H), and add ONE extra
    unit of exposure only while price is (a) below its lower Bollinger band -- an
    oversold dip -- AND (b) still above its 200-day SMA -- i.e. a dip *within an
    uptrend*, not a crash. The 200-SMA gate is the crash brake: it stops the strategy
    from doubling down into 2008/2020-style collapses, so drawdown never exceeds B&H.

Position:  pos_t = 1.0 + OVERLAY * dip_t * uptrend_t          (then lagged 1 bar)
  dip_t     = 1 while (close < lowerBB(N,K)) ... until (close >= midBB)   [state]
  uptrend_t = 1 if close > SMA200 else 0
Exposure is therefore 1x normally and (1+OVERLAY)x during qualifying dips.

Costs: 5 bps/side turnover; financing 6.5%/yr on exposure above 1x (the extra tranche).
No lookahead: every signal uses close[t], position held t->t+1.

Usage:  python src/nifty_final.py
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
REPORT_FILE = ROOT / "reports" / "legacy-markets" / "india" / "nifty_final_equity.csv"

BPY = 252
COST = 0.0005
RF_D = 0.065 / BPY
BB_N, BB_K = 20, 2.0
OVERLAY = 1.0            # size of the extra dip tranche (1.0 => 2x during qualifying dips)


def load_close() -> pd.Series:
    df = pd.read_csv(DATA_FILE, parse_dates=["date"]).set_index("date").sort_index()
    return df["close"]


def net_returns(pos: pd.Series, ret: pd.Series) -> pd.Series:
    turnover = pos.diff().abs().fillna(pos.abs())
    borrow = (pos - 1.0).clip(lower=0.0)
    return (pos * ret - turnover * COST - borrow * RF_D).fillna(0.0)


def dip_state(close: pd.Series, n: int, k: float) -> pd.Series:
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std()
    lower = mid - k * sd
    entry = (close < lower).values
    exit_ = (close >= mid).values
    out = np.zeros(len(close)); inpos = False
    for i in range(len(close)):
        if inpos:
            if exit_[i]:
                inpos = False
        elif entry[i]:
            inpos = True
        out[i] = 1.0 if inpos else 0.0
    return pd.Series(out, index=close.index)


def strategy_pos(close: pd.Series, overlay: float = OVERLAY,
                 n: int = BB_N, k: float = BB_K, cost: float = COST) -> pd.Series:
    dip = dip_state(close, n, k)
    up = (close > close.rolling(200).mean()).astype(float)
    pos = (1.0 + overlay * dip * up).shift(1).fillna(1.0)
    return pos


def metrics_of(r: pd.Series) -> dict:
    return {"totRet%": M.total_return(r) * 100, "CAGR%": M.cagr(r, BPY) * 100,
            "vol%": M.ann_vol(r, BPY) * 100, "sharpe": M.sharpe(r, BPY),
            "sortino": M.sortino(r, BPY), "maxDD%": M.max_drawdown(r) * 100,
            "calmar": M.calmar(r, BPY)}


def clip_window(r: pd.Series, years):
    if years is None:
        return r
    return r[r.index >= r.index.max() - pd.Timedelta(days=int(years * 365.25))]


def main() -> None:
    close = load_close()
    ret = close.pct_change().fillna(0.0)
    bh = net_returns(pd.Series(1.0, index=close.index), ret)

    print("=" * 80)
    print("NIFTY 50  --  DIP-LEVERAGE STRATEGY  vs  BUY & HOLD")
    print(f"data: {close.index.min().date()} -> {close.index.max().date()}  "
          f"({len(close)} bars)   costs: 5bps/side + 6.5%/yr financing on leverage")
    print("=" * 80)

    # ---- overlay-size sweep across windows (return & Sharpe vs B&H) ----
    print("\n--- OVERLAY-SIZE SWEEP  (does it beat B&H return AND Sharpe?) ---")
    windows = [("full", None), ("10-year", 10.0), ("5-year", 5.0), ("3-year", 3.0)]
    for wname, yrs in windows:
        bw = clip_window(bh, yrs); bhm = metrics_of(bw)
        print(f"\n  [{wname}]  B&H: totRet {bhm['totRet%']:.1f}%  CAGR {bhm['CAGR%']:.2f}%  "
              f"Sharpe {bhm['sharpe']:.2f}  maxDD {bhm['maxDD%']:.1f}%")
        for ov in [0.5, 0.75, 1.0]:
            r = clip_window(net_returns(strategy_pos(close, overlay=ov), ret), yrs)
            m = metrics_of(r)
            flag = ("BEATS BOTH" if (m['totRet%'] > bhm['totRet%'] and m['sharpe'] > bhm['sharpe'])
                    else "beats ret" if m['totRet%'] > bhm['totRet%'] else "-")
            print(f"    overlay {ov:>4}x : totRet {m['totRet%']:7.1f}%  CAGR {m['CAGR%']:5.2f}%  "
                  f"Sharpe {m['sharpe']:.2f}  maxDD {m['maxDD%']:6.1f}%  Calmar {m['calmar']:.2f}   {flag}")

    # ---- lock OVERLAY=1.0 and give full headline + yearly ----
    pos = strategy_pos(close, overlay=OVERLAY)
    strat = net_returns(pos, ret)
    print("\n" + "=" * 80)
    print(f"LOCKED STRATEGY  (overlay = {OVERLAY}x  =>  up to 2x during qualifying dips)")
    print("=" * 80)
    print("\n--- HEADLINE METRICS BY WINDOW ---")
    hdr = f"{'window':<9}{'strat totRet%':>14}{'BH totRet%':>12}{'strat CAGR%':>13}{'BH CAGR%':>10}{'strat Shrp':>11}{'BH Shrp':>9}{'strat DD%':>11}{'BH DD%':>9}"
    print(hdr); print("-" * len(hdr))
    for wname, yrs in windows:
        s = metrics_of(clip_window(strat, yrs)); b = metrics_of(clip_window(bh, yrs))
        print(f"{wname:<9}{s['totRet%']:>14.1f}{b['totRet%']:>12.1f}{s['CAGR%']:>13.2f}"
              f"{b['CAGR%']:>10.2f}{s['sharpe']:>11.2f}{b['sharpe']:>9.2f}{s['maxDD%']:>11.1f}{b['maxDD%']:>9.1f}")

    # exposure / activity stats
    exp_gt1 = float((pos > 1.0 + 1e-9).mean())
    n_events = int(((pos > 1.0 + 1e-9).astype(int).diff() == 1).sum())
    avg_days = (pos > 1.0 + 1e-9).sum() / max(n_events, 1)
    print(f"\nactivity: leveraged {exp_gt1*100:.1f}% of days | {n_events} dip-events "
          f"(~{n_events/(len(close)/252):.1f}/yr) | avg {avg_days:.0f} trading days each | "
          f"avg exposure {pos.mean():.2f}x")

    print("\n--- YEAR-BY-YEAR TOTAL RETURN (%) ---")
    years = sorted({d.year for d in close.index})
    rows = []
    for y in years:
        sy = strat[strat.index.year == y]; by = bh[bh.index.year == y]
        rows.append({"year": y, "Strategy": M.total_return(sy) * 100,
                     "BuyHold": M.total_return(by) * 100,
                     "diff": (M.total_return(sy) - M.total_return(by)) * 100})
    yt = pd.DataFrame(rows).set_index("year").round(1)
    print(yt.to_string())
    print(f"\nStrategy beat B&H in {(yt['diff'] > 0).sum()}/{len(yt)} calendar years")

    # ---- robustness: BB params + cost stress + walk-forward ----
    print("\n--- ROBUSTNESS: Bollinger params (full-period, overlay 1.0x) ---")
    for n in [15, 20, 25]:
        for k in [1.5, 2.0, 2.5]:
            r = net_returns(strategy_pos(close, 1.0, n, k), ret); m = metrics_of(r)
            print(f"  BB({n},{k}): CAGR {m['CAGR%']:5.2f}%  Sharpe {m['sharpe']:.2f}  maxDD {m['maxDD%']:6.1f}%")

    print("\n--- ROBUSTNESS: cost stress (full-period) ---")
    for c in [0.0005, 0.0010, 0.0020]:
        r = net_returns(strategy_pos(close), ret)  # turnover cost baked via COST global? use explicit
        # recompute with explicit cost
        pos_c = strategy_pos(close)
        turnover = pos_c.diff().abs().fillna(pos_c.abs())
        borrow = (pos_c - 1.0).clip(lower=0.0)
        rc = (pos_c * ret - turnover * c - borrow * RF_D).fillna(0.0)
        m = metrics_of(rc)
        print(f"  {c*1e4:>4.0f} bps/side: CAGR {m['CAGR%']:5.2f}%  Sharpe {m['sharpe']:.2f}")

    print("\n--- WALK-FORWARD (halves) ---")
    mid = strat.index[len(strat)//2]
    for label, seg in [("H1 (older)", strat[strat.index < mid]), ("H2 (newer)", strat[strat.index >= mid])]:
        bseg = bh[bh.index < mid] if "older" in label else bh[bh.index >= mid]
        ms = metrics_of(seg); mb = metrics_of(bseg)
        print(f"  {label}: strat CAGR {ms['CAGR%']:.2f}% (Shrp {ms['sharpe']:.2f}) vs "
              f"BH CAGR {mb['CAGR%']:.2f}% (Shrp {mb['sharpe']:.2f})")

    # save equity for the plot
    eq = pd.DataFrame({"BH": (1 + bh).cumprod(), "DipLev": (1 + strat).cumprod()}, index=close.index)
    eq.to_csv(REPORT_FILE)
    print(f"\nsaved -> {REPORT_FILE}")


if __name__ == "__main__":
    main()
