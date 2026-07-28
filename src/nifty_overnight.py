"""
Where does Nifty's return actually come from -- the overnight session or the
intraday session? Uses 18.9 years of daily OHLC (data_nifty.csv). This decides
whether an INTRADAY-only strategy is even plausible.

  overnight return (t) = open[t] / close[t-1] - 1     (hold from prior close to open)
  intraday  return (t) = close[t] / open[t]  - 1      (hold from open to close)
  (1+overnight)*(1+intraday) reconstructs the full close-to-close daily return.

Costs: an intraday round trip pays cost twice (entry+exit); overnight likewise.
We report gross first (the structural edge) then net at realistic cost levels.
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


def load() -> pd.DataFrame:
    return pd.read_csv(DATA_FILE, parse_dates=["date"]).set_index("date").sort_index()


def report(name: str, r: pd.Series):
    print(f"{name:<26} totRet {M.total_return(r)*100:8.1f}%  CAGR {M.cagr(r,BPY)*100:6.2f}%  "
          f"Sharpe {M.sharpe(r,BPY):5.2f}  maxDD {M.max_drawdown(r)*100:6.1f}%  "
          f"hit {100*(r>0).mean():4.1f}%")


def main():
    df = load()
    o, c = df["open"], df["close"]
    cc = c.pct_change().fillna(0.0)                       # close-to-close (full day)
    overnight = (o / c.shift(1) - 1).fillna(0.0)          # prev close -> open
    intraday = (c / o - 1).fillna(0.0)                    # open -> close

    print("=" * 84)
    print("NIFTY 50 -- OVERNIGHT vs INTRADAY return decomposition (2007-2026, GROSS)")
    print("=" * 84)
    report("Buy&Hold (close->close)", cc)
    report("Overnight only", overnight)
    report("Intraday only (long)", intraday)
    report("Intraday only (SHORT)", -intraday)

    tot_cc = M.total_return(cc)
    tot_on = M.total_return(overnight)
    tot_id = M.total_return(intraday)
    print(f"\nGross growth of 1 rupee:  full {1+tot_cc:6.2f}x   "
          f"overnight {1+tot_on:6.2f}x   intraday {1+tot_id:6.2f}x")
    print("mean per-session return:  overnight %.4f%%   intraday %.4f%%"
          % (overnight.mean()*100, intraday.mean()*100))

    # ---- net of costs: an intraday trade pays cost on entry AND exit ----
    print("\n" + "=" * 84)
    print("NET OF ROUND-TRIP COSTS  (cost charged twice: entry + exit)")
    print("=" * 84)
    for label, base in [("Overnight-long", overnight), ("Intraday-long", intraday),
                        ("Intraday-short", -intraday)]:
        print(f"\n{label}:")
        for c_side in [0.0002, 0.0005, 0.0010]:      # 2 / 5 / 10 bps per side
            r = base - 2 * c_side                    # pay each side every day
            print(f"   {c_side*1e4:>4.0f} bps/side  ->  CAGR {M.cagr(r,BPY)*100:6.2f}%  "
                  f"Sharpe {M.sharpe(r,BPY):5.2f}  totRet {M.total_return(r)*100:7.1f}%")

    # ---- by year: overnight vs intraday CAGR-ish (total return per year) ----
    print("\n" + "=" * 84)
    print("PER-YEAR TOTAL RETURN (%) -- GROSS")
    print("=" * 84)
    yrs = sorted({d.year for d in df.index})
    rows = []
    for y in yrs:
        m = df.index.year == y
        rows.append({"year": y,
                     "full": M.total_return(cc[m])*100,
                     "overnight": M.total_return(overnight[m])*100,
                     "intraday": M.total_return(intraday[m])*100})
    print(pd.DataFrame(rows).set_index("year").round(1).to_string())


if __name__ == "__main__":
    main()
