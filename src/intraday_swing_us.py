"""
Lower-timeframe (1h / 4h) LEVERAGED SWING strategy for US-index perps
(CoinDCX "global futures": NASDAQ-100 / S&P). Holds hours-to-days, not minutes.

Data: real 1h OHLC from Yahoo (~2.9y, the max free hourly history), resampled to 4h
by fixed 4-bar grouping (avoids empty overnight bins). Underlying is regular-session
hourly, so overnight/weekend gap risk is UNDERSTATED here -- see the note printed.

Perp modeling (honest):
  - pos = target leverage held over the next bar (causal: signal on bar close, act next bar).
  - COST bps/side on turnover (perp taker + slippage) -- charged on every change.
  - FUNDING FUND_ANNUAL/yr on leverage above 1x, accrued per bar.
  - LIQUIDATION on the bar's low: a long at leverage L dies if low/prevclose-1 <= -(1-MAINT)/L.

Strategies (all long / de-risked, leverage L is swept 2x..5x):
  TREND    regime = close>EMA(slow); hold L in uptrend, L_down (<1) below it (softgate).
  DONCH    Donchian breakout swing: enter L on N-bar-high breakout in uptrend, exit on
           M-bar-low OR ATR trailing stop; flat otherwise.
  DIP      buy the lower Bollinger band in an uptrend at L, exit at the mid band.

Reports: 3yr headline metrics per leverage, month-by-month return matrix for the
recommended pick, and per-year returns.

Usage:  python src/intraday_swing_us.py [ndx|qqq|spx] [1h|4h]
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

COST = 0.0005          # 5 bps/side (perp taker+slippage on an index perp)
FUND_ANNUAL = 0.10     # 10%/yr financing on leverage>1x (perps run a bit hot)
MAINT = 0.005          # 0.5% maintenance margin
LEVS = [2.0, 3.0, 4.0, 5.0]


def load(which: str) -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / f"intr_{which}_1h.csv", parse_dates=["ts"]).set_index("ts").sort_index()
    return df


def resample_nbars(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Group every n consecutive rows into one bar (clean OHLC, no empty bins)."""
    if n == 1:
        return df
    g = np.arange(len(df)) // n
    out = df.groupby(g).agg(open=("open", "first"), high=("high", "max"),
                            low=("low", "min"), close=("close", "last"),
                            volume=("volume", "sum"))
    out.index = df.index[np.arange(len(df)) // n * n][:len(out)] if False else \
        df.groupby(g).apply(lambda x: x.index[-1])
    return out


def bpy_of(idx: pd.DatetimeIndex) -> float:
    years = (idx.max() - idx.min()).days / 365.25
    return len(idx) / max(years, 1e-9)


def lev_returns(pos, ret, low_ret, bpy):
    fund_d = FUND_ANNUAL / bpy
    turnover = pos.diff().abs().fillna(pos.abs())
    borrow = (pos - 1.0).clip(lower=0.0)
    with np.errstate(divide="ignore"):
        liq_thresh = np.where(pos.values > 0, -(1 - MAINT) / np.where(pos.values > 0, pos.values, np.nan), -np.inf)
    liq = (low_ret.values <= liq_thresh) & (pos.values > 0)
    net = pos * ret - turnover * COST - borrow * fund_d
    net = net.where(~pd.Series(liq, index=pos.index), -1.0)
    return net.fillna(0.0), int(liq.sum())


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def atr(df, n):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False, min_periods=n).mean()


def pos_trend(df, L, slow=50, down=0.5):
    c = df["close"]
    reg = (c > ema(c, slow)).astype(float)
    return (reg * L + (1 - reg) * down).shift(1).fillna(0.0)


def pos_donch(df, L, n_hi=20, n_lo=10, slow=50, atr_n=14, atr_k=3.0):
    c = df["close"]
    reg = (c > ema(c, slow)).values
    hi = c.rolling(n_hi).max().shift(1).values
    lo = c.rolling(n_lo).min().shift(1).values
    a = atr(df, atr_n).values
    cv = c.values
    pos = np.zeros(len(c)); inpos = False; trail = 0.0
    for i in range(len(c)):
        if inpos:
            if not np.isnan(a[i]):
                trail = max(trail, cv[i] - atr_k * a[i])
            if (not np.isnan(lo[i]) and cv[i] < lo[i]) or cv[i] < trail:
                inpos = False
        else:
            if reg[i] and not np.isnan(hi[i]) and cv[i] > hi[i]:
                inpos = True
                trail = cv[i] - atr_k * a[i] if not np.isnan(a[i]) else 0.0
        pos[i] = L if inpos else 0.0
    return pd.Series(pos, index=c.index).shift(1).fillna(0.0)


def pos_levbh(df, L):
    """Constant leveraged buy&hold -- not a 'swing' but the honest leverage benchmark."""
    return pd.Series(L, index=df["close"].index)


def pos_dip(df, L, n=20, k=2.0, slow=50):
    c = df["close"]
    mid = c.rolling(n).mean(); sd = c.rolling(n).std()
    lower = mid - k * sd
    reg = (c > ema(c, slow)).values
    e = (c < lower).values; x = (c >= mid).values
    pos = np.zeros(len(c)); inpos = False
    for i in range(len(c)):
        inpos = (inpos and not x[i]) or (not inpos and e[i] and reg[i])
        pos[i] = L if inpos else 0.0
    return pd.Series(pos, index=c.index).shift(1).fillna(0.0)


def monthly_matrix(r, label):
    m = r.groupby([r.index.year, r.index.month]).apply(lambda x: M.total_return(x) * 100)
    m.index.names = ["year", "month"]
    mat = m.unstack("month")
    mat.columns = [pd.Timestamp(2000, c, 1).strftime("%b") for c in mat.columns]
    yearly = r.groupby(r.index.year).apply(lambda x: M.total_return(x) * 100)
    mat["YEAR"] = yearly
    print(f"\n### MONTH-ON-MONTH RETURN (%)  --  {label}")
    print(mat.round(1).to_string(na_rep="   ."))
    pos = m[m.notna()]
    print(f"  months positive: {100*(pos>0).mean():.0f}%  |  avg {pos.mean():.2f}%  |  "
          f"best {pos.max():.1f}%  worst {pos.min():.1f}%")


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "ndx"
    tf = sys.argv[2] if len(sys.argv) > 2 else "4h"
    nbars = {"1h": 1, "4h": 4}[tf]
    name = {"ndx": "NASDAQ-100", "qqq": "QQQ (NDX-100 ETF)", "spx": "S&P 500"}[which]

    raw = load(which)
    df = resample_nbars(raw, nbars)
    c = df["close"]
    ret = c.pct_change().fillna(0.0)
    low_ret = (df["low"] / c.shift(1) - 1).fillna(0.0)
    bpy = bpy_of(df.index)
    years = (df.index.max() - df.index.min()).days / 365.25

    print("=" * 100)
    print(f"{name}  --  {tf} candles, LEVERAGED SWING  ({df.index.min().date()} -> "
          f"{df.index.max().date()}, {len(df)} bars, {years:.2f}y)")
    print(f"cost {COST*1e4:.0f}bp/side | funding {FUND_ANNUAL*100:.0f}%/yr on lev>1 | "
          f"liquidation on bar low | ~{bpy:.0f} bars/yr")
    print("=" * 100)

    # buy & hold 1x reference
    r_bh, _ = lev_returns(pd.Series(1.0, index=c.index), ret, low_ret, bpy)

    builders = {"TREND": pos_trend, "DONCH": pos_donch, "DIP": pos_dip, "LEVBH": pos_levbh}
    print(f"\n{'strat':<7}{'lev':>4}{'CAGR%':>9}{'totRet%':>9}{'vol%':>7}{'Sharpe':>8}"
          f"{'Sortino':>8}{'maxDD%':>8}{'Calmar':>8}{'expo%':>7}{'liq':>5}")
    print("-" * 88)
    best = None
    rows = []
    for sname, fn in builders.items():
        for L in LEVS:
            pos = fn(df, L)
            r, nliq = lev_returns(pos, ret, low_ret, bpy)
            expo = (pos.abs() > 1e-9).mean() * 100
            row = dict(strat=sname, lev=L, r=r, nliq=nliq, expo=expo,
                       cagr=M.cagr(r, bpy), tot=M.total_return(r), vol=M.ann_vol(r, bpy),
                       sharpe=M.sharpe(r, bpy), sortino=M.sortino(r, bpy),
                       mdd=M.max_drawdown(r), calmar=M.calmar(r, bpy))
            rows.append(row)
            print(f"{sname:<7}{L:>4.0f}{row['cagr']*100:>9.1f}{row['tot']*100:>9.0f}"
                  f"{row['vol']*100:>7.0f}{row['sharpe']:>8.2f}{row['sortino']:>8.2f}"
                  f"{row['mdd']*100:>8.1f}{row['calmar']:>8.2f}{expo:>7.0f}{nliq:>5d}")
    print(f"{'BH':<7}{1:>4.0f}{M.cagr(r_bh,bpy)*100:>9.1f}{M.total_return(r_bh)*100:>9.0f}"
          f"{M.ann_vol(r_bh,bpy)*100:>7.0f}{M.sharpe(r_bh,bpy):>8.2f}{M.sortino(r_bh,bpy):>8.2f}"
          f"{M.max_drawdown(r_bh)*100:>8.1f}{M.calmar(r_bh,bpy):>8.2f}{100:>7.0f}{0:>5d}")

    # pick best genuinely-invested config that beats BH on RETURN (expo>=50%, 0 liq),
    # ranked by Sharpe; if none beats BH return, say so and pick best risk-adjusted.
    bh_tot = M.total_return(r_bh)
    bh_sh = M.sharpe(r_bh, bpy)
    invested = [x for x in rows if x["nliq"] == 0 and x["expo"] >= 50]
    beat = [x for x in invested if x["tot"] > bh_tot and x["sharpe"] >= 0.9 * bh_sh]
    if beat:
        best = max(beat, key=lambda x: x["sharpe"])
        verdict = "beats buy&hold on return with comparable risk"
    else:
        best = max(invested, key=lambda x: x["sharpe"])
        verdict = ("NOTHING beat 1x buy&hold on BOTH return and risk in this window; "
                   "this is the best leveraged option")
    tag = f"{best['strat']} {best['lev']:.0f}x"
    print(f"\n>>> BEST LEVERAGED PICK: {tag}  (CAGR {best['cagr']*100:.1f}%, "
          f"Sharpe {best['sharpe']:.2f}, maxDD {best['mdd']*100:.1f}%, {best['nliq']} liq)")
    print(f"    verdict: {verdict}")
    print(f"    vs 1x Buy&Hold: CAGR {M.cagr(r_bh,bpy)*100:.1f}%, Sharpe {bh_sh:.2f}, "
          f"maxDD {M.max_drawdown(r_bh)*100:.1f}%")

    # best GENUINE swing (signal-based, excludes LEVBH constant-hold), by return
    swing_pool = [x for x in rows if x["strat"] != "LEVBH" and x["nliq"] == 0 and x["expo"] >= 30]
    bswing = max(swing_pool, key=lambda x: x["tot"])
    stag = f"{bswing['strat']} {bswing['lev']:.0f}x"

    monthly_matrix(best["r"], f"{name} {tf}  {tag}  [best leverage pick]")
    monthly_matrix(bswing["r"], f"{name} {tf}  {stag}  [best genuine swing]")
    monthly_matrix(r_bh, f"{name} {tf}  Buy&Hold 1x (reference)")

    print("\nNOTE: hourly data is regular-session only, so overnight/weekend GAP risk is")
    print("UNDERSTATED. On a real 24/7 CoinDCX perp at high leverage a gap can liquidate you.")


if __name__ == "__main__":
    main()
