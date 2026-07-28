"""
Intraday Opening-Range-Breakout (ORB) backtest for Nifty & F&O stocks.

Why ORB (and why selective): the overnight/intraday decomposition (nifty_overnight.py)
showed (a) the intraday session has NEGATIVE structural drift -> favors shorts, and
(b) any strategy that trades EVERY day dies on costs. ORB trades at most once/day and
only when price breaks out of the first part of the session, so it is selective and
targets a large move relative to the cost hurdle.

Rules (per day):
  - opening range = high/low of the first OR_BARS bars of the session.
  - after the opening range, the FIRST bar that closes above the range high -> go LONG;
    the first that closes below the range low -> go SHORT (side filter applies).
  - enter at that bar's close; hard stop at the opposite side of the opening range;
    otherwise exit at the day's last close (square-off, no overnight risk).
  - at most one trade per day.
Costs: `CSIDE` per side on notional, charged on entry AND exit (round trip = 2*CSIDE).

Data: intraday JSON pulled from Yahoo (parse_json). 15m capped at 60 days by Yahoo;
60m gives ~2 years -> use 60m for the longer monthly/yearly view, 15m as a fine cross-check.

Usage:  python src/nifty_intraday.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "markets" / "india"

BPY = 252
CSIDE = 0.0006          # 6 bps per side (blended brokerage+STT+fees+slippage, intraday)


def parse_json(path: str) -> pd.DataFrame:
    j = json.load(open(path)); res = j["chart"]["result"][0]
    ts = res["timestamp"]; q = res["indicators"]["quote"][0]
    idx = pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata")
    df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"],
                       "close": q["close"], "volume": q["volume"]}, index=idx).dropna()
    df = df[(df.index.time >= pd.Timestamp("09:15").time()) &
            (df.index.time <= pd.Timestamp("15:30").time())]
    return df


def orb_day(day_df: pd.DataFrame, or_bars: int, side: str, cside: float,
            mode: str = "breakout"):
    """Return (trade_ret_net, direction, hit_stop) for one day, or None if no trade.

    mode='breakout': go WITH the break (long above range, short below).
    mode='fade'    : go AGAINST the break (short the up-break, long the down-break),
                     betting the breakout fails and price reverts; stop placed one
                     range-width beyond the broken level.
    """
    if len(day_df) < or_bars + 2:
        return None
    o_hi = day_df["high"].iloc[:or_bars].max()
    o_lo = day_df["low"].iloc[:or_bars].min()
    width = max(o_hi - o_lo, 1e-9)
    rest = day_df.iloc[or_bars:]
    for i in range(len(rest)):
        bar = rest.iloc[i]
        up_break = bar["close"] > o_hi
        dn_break = bar["close"] < o_lo
        if mode == "breakout":
            if up_break and side in ("long", "both"):
                entry, stop, direction = bar["close"], o_lo, 1
            elif dn_break and side in ("short", "both"):
                entry, stop, direction = bar["close"], o_hi, -1
            else:
                continue
        else:  # fade
            if up_break and side in ("short", "both"):
                entry, stop, direction = bar["close"], o_hi + width, -1
            elif dn_break and side in ("long", "both"):
                entry, stop, direction = bar["close"], o_lo - width, 1
            else:
                continue
        # walk remaining bars: stop or square-off at last close
        exit_price = day_df["close"].iloc[-1]
        for j in range(i + 1, len(rest)):
            b = rest.iloc[j]
            if direction == 1 and b["low"] <= stop:
                exit_price = stop; break
            if direction == -1 and b["high"] >= stop:
                exit_price = stop; break
        gross = direction * (exit_price / entry - 1)
        net = gross - 2 * cside
        return net, direction, (exit_price == stop)
    return None


def backtest(df: pd.DataFrame, or_bars: int, side: str, cside: float = CSIDE,
             mode: str = "breakout"):
    days = df.index.normalize()
    daily = {}
    dirs = []; stops = []
    for d, g in df.groupby(days):
        res = orb_day(g, or_bars, side, cside, mode)
        if res is None:
            daily[d] = 0.0
        else:
            net, direction, hitstop = res
            daily[d] = net; dirs.append(direction); stops.append(hitstop)
    r = pd.Series(daily).sort_index()
    r.index = pd.to_datetime(r.index)
    trades = r[r != 0.0]
    stats = {
        "trades": int((r != 0.0).sum()),
        "days": int(len(r)),
        "trade_rate%": 100 * (r != 0.0).mean(),
        "win%": 100 * (trades > 0).mean() if len(trades) else 0.0,
        "avg_trade%": trades.mean() * 100 if len(trades) else 0.0,
        "totRet%": M.total_return(r) * 100,
        "sharpe": M.sharpe(r, BPY),
        "maxDD%": M.max_drawdown(r) * 100,
        "shorts": sum(1 for d in dirs if d < 0),
        "longs": sum(1 for d in dirs if d > 0),
    }
    return r, stats


def show(title, df, or_bars, mode="breakout"):
    print(f"\n{title}  (opening range = {or_bars} bars, mode = {mode})")
    print(f"  {'side':<6}{'trades':>7}{'trade%':>8}{'win%':>7}{'avgTr%':>8}{'totRet%':>9}{'Sharpe':>8}{'maxDD%':>8}  L/S")
    for side in ["long", "short", "both"]:
        r, s = backtest(df, or_bars, side, mode=mode)
        print(f"  {side:<6}{s['trades']:>7}{s['trade_rate%']:>7.0f}%{s['win%']:>6.0f}%"
              f"{s['avg_trade%']:>8.3f}{s['totRet%']:>9.1f}{s['sharpe']:>8.2f}{s['maxDD%']:>8.1f}"
              f"  {s['longs']}/{s['shorts']}")


def monthly_yearly(df, or_bars, side, cside=CSIDE, mode="breakout"):
    r, _ = backtest(df, or_bars, side, cside, mode)
    print(f"\n  Per-YEAR total return (%), side={side}, mode={mode}:")
    yy = r.groupby(r.index.year).apply(lambda x: M.total_return(x) * 100).round(1)
    print("   ", yy.to_dict())
    print(f"  Per-MONTH win rate: {100*(r.groupby([r.index.year,r.index.month]).apply(lambda x: M.total_return(x)).gt(0)).mean():.0f}% "
          f"of months positive")
    monthly = r.groupby([r.index.year, r.index.month]).apply(lambda x: M.total_return(x) * 100)
    print(f"  avg month {monthly.mean():.2f}%   median {monthly.median():.2f}%   "
          f"best {monthly.max():.1f}%   worst {monthly.min():.1f}%")


def main():
    print("=" * 88)
    print(f"INTRADAY OPENING-RANGE-BREAKOUT  --  costs {CSIDE*1e4:.0f} bps/side (round trip {2*CSIDE*1e4:.0f} bps)")
    print("=" * 88)

    nifty60 = parse_json(DATA_DIR / "intr_nifty_60m.json")
    print(f"\n### NIFTY 50 -- hourly bars, {nifty60.index.normalize().nunique()} days "
          f"({nifty60.index.min().date()} -> {nifty60.index.max().date()})")
    show("NIFTY 60m", nifty60, or_bars=1, mode="breakout")
    show("NIFTY 60m", nifty60, or_bars=1, mode="fade")

    nifty15 = parse_json(DATA_DIR / "intr_nifty_15m.json")
    print(f"\n### NIFTY 50 -- 15-min bars, {nifty15.index.normalize().nunique()} days (cross-check, short sample)")
    show("NIFTY 15m", nifty15, or_bars=2, mode="fade")

    for name, path in [("RELIANCE", DATA_DIR / "intr_reliance_15m.json"),
                       ("HDFCBANK", DATA_DIR / "intr_hdfcbank_15m.json")]:
        d = parse_json(path)
        print(f"\n### {name} -- 15-min bars, {d.index.normalize().nunique()} days (F&O stock, short sample)")
        show(f"{name} 15m breakout", d, or_bars=2, mode="breakout")
        show(f"{name} 15m fade", d, or_bars=2, mode="fade")

    print("\n" + "=" * 88)
    print("MONTHLY / YEARLY CONSISTENCY  (Nifty 60m, FADE)")
    print("=" * 88)
    monthly_yearly(nifty60, or_bars=1, side="both", mode="fade")

    print("\n" + "=" * 88)
    print("COST SENSITIVITY  (Nifty 60m, FADE, side=both, opening range 1 bar)")
    print("=" * 88)
    for c in [0.0003, 0.0006, 0.0010, 0.0015]:
        _, s = backtest(nifty60, 1, "both", c, mode="fade")
        print(f"  {c*1e4:>4.0f} bps/side (rt {2*c*1e4:.0f}): totRet {s['totRet%']:7.1f}%  "
              f"Sharpe {s['sharpe']:5.2f}  avgTrade {s['avg_trade%']:.3f}%")


if __name__ == "__main__":
    main()
