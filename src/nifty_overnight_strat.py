"""
The one session-based edge that SURVIVES costs: hold Nifty FUTURES overnight,
square off at the open. Tested on 18.9 years of daily OHLC (data_nifty.csv).

Not "intraday" (you hold through the night), but it is the profitable mirror image:
the decomposition showed the intraday session (open->close) has NEGATIVE drift while
the overnight session (close->open) carries essentially all of Nifty's return.

Why it can work net of costs where intraday ORB cannot:
  - avg overnight gross return ~0.094%/day; break-even round-trip cost ~9.4 bps.
  - Nifty/BankNifty FUTURES round-trip cost ~3-4 bps (STT 0.02% on sell side only,
    + tiny exchange/GST/stamp, + ~1 tick slippage). Well under the 9.4 bps hurdle.
  - Equity/ETF costs (~10 bps round trip) would kill it -> must use futures.

Variants:
  ON_all      hold overnight EVERY day.
  ON_regime   hold overnight only when prev close > 200-DMA (skip bear-market gaps).
Reports net metrics at several futures cost levels, per-year returns, monthly hit rate.
The big risk this carries that pure intraday does not: overnight GAP risk.
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

BPY = 252


def load():
    return pd.read_csv(DATA_FILE, parse_dates=["date"]).set_index("date").sort_index()


def main():
    df = load()
    o, c = df["open"], df["close"]
    overnight = (o / c.shift(1) - 1).fillna(0.0)              # prev close -> open
    regime = (c.shift(1) > c.shift(1).rolling(200).mean()).astype(float).fillna(0.0)

    print("=" * 82)
    print("OVERNIGHT-FUTURES STRATEGY  (hold Nifty futures close->open, square off at open)")
    print(f"data {c.index.min().date()} -> {c.index.max().date()}  ({len(c)} days)")
    print("=" * 82)

    print("\n--- NET METRICS vs futures round-trip cost (cost paid every held day) ---")
    hdr = f"{'variant':<12}{'rt cost':>9}{'CAGR%':>8}{'Sharpe':>8}{'maxDD%':>9}{'hit%':>7}{'expo%':>7}"
    print(hdr); print("-" * len(hdr))
    for name, base in [("ON_all", overnight), ("ON_regime", overnight * regime)]:
        for rt in [0.00035, 0.0005, 0.0008]:                 # 3.5 / 5 / 8 bps round trip
            held = (base != 0.0) if name == "ON_regime" else pd.Series(True, index=base.index)
            r = base - rt * held.astype(float)               # charge cost only on held days
            expo = held.mean() * 100
            print(f"{name:<12}{rt*1e4:>7.1f}bp{M.cagr(r,BPY)*100:>8.2f}{M.sharpe(r,BPY):>8.2f}"
                  f"{M.max_drawdown(r)*100:>9.1f}{100*(r>0).mean():>7.1f}{expo:>7.1f}")

    # lock ON_regime at 3.5 bps for detail
    rt = 0.00035
    held = (overnight * regime != 0.0)
    r = overnight * regime - rt * held.astype(float)
    bh = c.pct_change().fillna(0.0)
    print("\n--- LOCKED: ON_regime @ 3.5 bps round trip ---")
    print(f"CAGR {M.cagr(r,BPY)*100:.2f}%  Sharpe {M.sharpe(r,BPY):.2f}  "
          f"Sortino {M.sortino(r,BPY):.2f}  maxDD {M.max_drawdown(r)*100:.1f}%  "
          f"exposure {held.mean()*100:.0f}% of days")
    print(f"(for reference, buy&hold CAGR {M.cagr(bh,BPY)*100:.2f}%  Sharpe {M.sharpe(bh,BPY):.2f}  "
          f"maxDD {M.max_drawdown(bh)*100:.1f}%)")

    print("\n--- PER-YEAR NET RETURN (%)  ON_regime @3.5bp vs Buy&Hold ---")
    yrs = sorted({d.year for d in df.index})
    rows = []
    for y in yrs:
        m = df.index.year == y
        rows.append({"year": y, "Overnight": M.total_return(r[m]) * 100,
                     "BuyHold": M.total_return(bh[m]) * 100})
    yt = pd.DataFrame(rows).set_index("year").round(1)
    yt["diff"] = (yt["Overnight"] - yt["BuyHold"]).round(1)
    print(yt.to_string())
    print(f"\npositive years: {(yt['Overnight']>0).sum()}/{len(yt)}   "
          f"beat B&H: {(yt['diff']>0).sum()}/{len(yt)}")
    monthly = r.groupby([r.index.year, r.index.month]).apply(lambda x: M.total_return(x)*100)
    print(f"monthly: {100*(monthly>0).mean():.0f}% of months positive  "
          f"(avg {monthly.mean():.2f}%, worst {monthly.min():.1f}%, best {monthly.max():.1f}%)")

    print("\nNOTE: this carries OVERNIGHT GAP RISK (you hold through the night). The 200-DMA")
    print("gate skips bear-market gaps; worst single overnight loss below:")
    worst = (overnight[held]).nsmallest(5)
    for d, v in worst.items():
        print(f"   {d.date()}  {v*100:+.2f}%")


if __name__ == "__main__":
    main()
