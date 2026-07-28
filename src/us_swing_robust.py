"""
Robustness ("regression") test for the SG strategy: LEV_UP x while close>SMA,
1x otherwise (CoinDCX 1x floor). Is the edge real or curve-fit? Six stress axes:

  1. PARAMETER SENSITIVITY  -- SMA window {100,150,200,250} x up-lev {1.5,2.0,2.5}.
     A real edge is a broad plateau, not one lucky cell.
  2. MULTI-WINDOW           -- 3y / 5y / 10y trailing; does it beat B&H in each?
  3. CROSS-ASSET            -- NASDAQ-100 vs S&P 500 (independent index).
  4. COST / FUNDING STRESS  -- push fees and perp funding far past realistic.
  5. WALK-FORWARD           -- disjoint 2-year blocks; beat-B&H hit-rate per block.
  6. LIQUIDATION            -- any wipeouts at 2x anywhere.

Same honest perp engine as us_swing_10yr (cost, funding on lev>1, liq on daily low).

Usage:  python src/us_swing_robust.py
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

BPY = 252
MAINT = 0.005


def load(which):
    f = DATA_DIR / ("data_ndx.csv" if which == "ndx" else "data_spx.csv")
    return pd.read_csv(f, parse_dates=["date"]).set_index("date").sort_index()


def lev_returns(pos, ret, low_ret, cost, fund_annual):
    fund_d = fund_annual / BPY
    turnover = pos.diff().abs().fillna(pos.abs())
    borrow = (pos - 1.0).clip(lower=0.0)
    with np.errstate(divide="ignore"):
        thr = np.where(pos.values > 0, -(1 - MAINT) / np.where(pos.values > 0, pos.values, np.nan), -np.inf)
    liq = (low_ret.values <= thr) & (pos.values > 0)
    net = pos * ret - turnover * cost - borrow * fund_d
    net = net.where(~pd.Series(liq, index=pos.index), -1.0)
    return net.fillna(0.0), int(liq.sum())


def sg_pos(close, sma_n, lev_up):
    reg = (close > close.rolling(sma_n).mean()).astype(float)
    return (reg * lev_up + (1 - reg) * 1.0).shift(1).fillna(1.0)


def clip_win(s, years):
    return s[s.index >= s.index.max() - pd.Timedelta(days=int(years * 365.25))]


def perf(r):
    return M.cagr(r, BPY) * 100, M.sharpe(r, BPY), M.max_drawdown(r) * 100


def main():
    COST, FUND = 0.0004, 0.08
    for which in ["ndx", "spx"]:
        name = "NASDAQ-100" if which == "ndx" else "S&P 500"
        df = load(which)
        close = df["close"]
        ret = close.pct_change().fillna(0.0)
        low_ret = (df["low"] / close.shift(1) - 1).fillna(0.0)

        print("\n" + "#" * 92)
        print(f"# {name}  ({close.index.min().date()} -> {close.index.max().date()})")
        print("#" * 92)

        # ---- 1. PARAMETER SENSITIVITY (last 10y, CAGR% / Sharpe) ----
        print("\n[1] PARAMETER SENSITIVITY  -- last 10y, cell = CAGR% (Sharpe).  BH shown for ref")
        bh10 = clip_win(lev_returns(pd.Series(1.0, index=close.index), ret, low_ret, COST, FUND)[0], 10)
        bc, bs, _ = perf(bh10)
        print(f"    Buy&Hold 1x: {bc:.1f}% (Sharpe {bs:.2f})")
        hdr = "    SMA \\ up-lev" + "".join(f"{L:>16.1f}x" for L in [1.5, 2.0, 2.5])
        print(hdr)
        for n in [100, 150, 200, 250]:
            cells = []
            for L in [1.5, 2.0, 2.5]:
                r = clip_win(lev_returns(sg_pos(close, n, L), ret, low_ret, COST, FUND)[0], 10)
                c, s, _ = perf(r)
                cells.append(f"{c:>8.1f} ({s:>4.2f})")
            print(f"    SMA-{n:<9}" + "  ".join(cells))

        # ---- 2. MULTI-WINDOW (SG 2.0x vs BH) ----
        print("\n[2] MULTI-WINDOW  -- SG 2.0x (SMA-200) vs Buy&Hold 1x")
        print(f"    {'window':<8}{'SG CAGR%':>10}{'BH CAGR%':>10}{'SG Shrp':>9}{'BH Shrp':>9}"
              f"{'SG DD%':>9}{'BH DD%':>9}{'beat?':>7}")
        pos2 = sg_pos(close, 200, 2.0)
        r_sg_full, _ = lev_returns(pos2, ret, low_ret, COST, FUND)
        r_bh_full, _ = lev_returns(pd.Series(1.0, index=close.index), ret, low_ret, COST, FUND)
        for w in [3, 5, 10]:
            sg, bh = clip_win(r_sg_full, w), clip_win(r_bh_full, w)
            sc, ss, sd = perf(sg); bcc, bss, bd = perf(bh)
            beat = "Y" if M.total_return(sg) > M.total_return(bh) else "-"
            print(f"    {str(w)+'y':<8}{sc:>10.1f}{bcc:>10.1f}{ss:>9.2f}{bss:>9.2f}"
                  f"{sd:>9.1f}{bd:>9.1f}{beat:>7}")

        # ---- 4. COST / FUNDING STRESS (last 10y, SG 2.0x) ----
        print("\n[4] COST / FUNDING STRESS  -- SG 2.0x, last 10y")
        print(f"    {'cost bp/side':>13}{'funding %/yr':>14}{'CAGR%':>9}{'Sharpe':>9}{'vs BH CAGR':>12}")
        for cost, fund in [(0.0004, 0.08), (0.0010, 0.15), (0.0020, 0.30), (0.0030, 0.50)]:
            r = clip_win(lev_returns(pos2, ret, low_ret, cost, fund)[0], 10)
            c, s, _ = perf(r)
            print(f"    {cost*1e4:>11.0f}bp{fund*100:>12.0f}%{c:>9.1f}{s:>9.2f}{c-bc:>+11.1f}")

        # ---- 5. WALK-FORWARD (disjoint 2-year blocks) ----
        print("\n[5] WALK-FORWARD  -- disjoint 2y blocks, SG 2.0x vs BH (total return %)")
        yrs = sorted({d.year for d in close.index})
        blocks = [(y, y + 1) for y in range(yrs[0] + 1, yrs[-1], 2)]  # skip warmup year
        wins = 0; tot = 0
        for a, b in blocks:
            m = (close.index.year >= a) & (close.index.year <= b)
            if m.sum() < 100:
                continue
            sg = M.total_return(r_sg_full[m]) * 100
            bh = M.total_return(r_bh_full[m]) * 100
            tot += 1; wins += (sg > bh)
            print(f"    {a}-{b}:  SG {sg:>7.1f}%   BH {bh:>7.1f}%   {'beat' if sg>bh else '    '}")
        print(f"    --> SG 2.0x beat B&H in {wins}/{tot} disjoint 2y blocks")

        # ---- 6. LIQUIDATION across leverage ----
        print("\n[6] LIQUIDATION CHECK  -- full history")
        for L in [1.5, 2.0, 2.5, 3.0]:
            _, nliq = lev_returns(sg_pos(close, 200, L), ret, low_ret, COST, FUND)
            print(f"    SG {L:.1f}x: {nliq} liquidations")


if __name__ == "__main__":
    main()
