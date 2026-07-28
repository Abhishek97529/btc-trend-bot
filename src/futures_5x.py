"""
Leveraged BTC/USDT perpetual-futures variant of `trend_ensemble`, MAX 5x.

Every backtest parameter is IDENTICAL to the locked spot strategy:
  - same 7 trend votes, same threshold (0.50), same dates (2018-06-01 -> now)
  - same fees (0.10%) + slippage (5 bps), charged on the *leveraged* turnover
  - same daily-bar, act-on-next-bar, no-lookahead execution

What's added (the things spot ignores and leverage makes lethal):
  - LIQUIDATION on the intraday low: a long dies when leverage * adverse_move
    reaches -(1 - maintenance).
  - FUNDING paid daily on the leveraged notional (~11.7%/yr measured on Binance).

"max lev = 5x" is treated as a CEILING, not a constant. We compare several ways of
sizing exposure up to that ceiling, because a *constant* 5x is already known to
liquidate (2021-01-11, see LOCKED.md):

  LONG/FLAT (matches the locked spot direction) -- long when votes agree, else flat:
    1. constant_5x    lev = 5 whenever the gate is on              (the naive version)
    2. frac_5x        lev = agreement_fraction * 5                 (scale by conviction)
    3. voltarget_5x   lev = clip(target_vol / realized_vol, 0, 5)  (scale by risk)

  LONG/SHORT (futures-only, uses `trend_ls` direction) -- long when votes agree,
  SHORT when they don't; never flat after warmup:
    4. ls_constant_5x  lev = +/-5
    5. ls_voltarget_5x lev = direction * clip(target_vol / realized_vol, 0, 5)

Short mechanics modeled honestly: a short liquidates on the intraday HIGH (a rally),
and a short RECEIVES funding (sign flips) instead of paying it.

Usage:  python src/futures_5x.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_fetch import fetch_klines
from strategies_v2 import _trend_votes, trend_ensemble
from indicators import rolling_vol
import metrics as M

BPY = 365
FEE, SLIP = 0.001, 0.0005
FUNDING_DAILY = 0.117 / 365      # ~11.7%/yr on notional (measured from real Binance funding)
MAINT = 0.005                    # ~0.5% maintenance margin
MAX_LEV = 5.0
THRESHOLD = 0.5
START = "2018-06-01"


def leveraged_returns(df, lev, underlying, low_ret, high_ret):
    """Daily net return series for a signed leveraged perp (long +, short -, flat 0).

    `lev` is the per-bar signed target leverage already sized. A LONG liquidates on
    the intraday LOW; a SHORT liquidates on the intraday HIGH (a rally). Funding is
    paid on longs and RECEIVED on shorts (the sign flips out naturally). Once
    liquidated, every subsequent return is 0 (the account is dead)."""
    rets = np.zeros(len(lev))
    prev_notional = 0.0
    liq_idx = None
    for t in range(len(lev)):
        notional = float(lev.iloc[t])
        # intraday liquidation: worst point is the low for a long, the high for a short
        adverse = low_ret.iloc[t] if notional > 0 else high_ret.iloc[t]
        if notional != 0 and notional * adverse <= -(1 - MAINT):
            rets[t] = -1.0
            liq_idx = t
            break
        day_pnl = notional * underlying.iloc[t]
        funding = FUNDING_DAILY * notional          # >0 pays (long), <0 receives (short)
        turn_cost = abs(notional - prev_notional) * (FEE + SLIP)
        prev_notional = notional
        rets[t] = day_pnl - funding - turn_cost
    return pd.Series(rets, index=df.index), liq_idx


def count_trades(lev):
    """Direction changes = trades (covers long<->flat and long<->short flips)."""
    sign = np.sign(lev)
    return int((sign != sign.shift(1).fillna(0)).sum() - (sign.iloc[0] == 0))


CAPS = (2.0, 3.0, 5.0)   # leverage ceilings to compare
VT_TARGET = 1.0          # vol target (annualised); tv=1.0 had the best Sharpe at 5x


def build_leverage(df, cap):
    """Return the candidate per-bar SIGNED leverage series for a given leverage CAP.

    All are shifted one bar (act on t+1) to match the spot engine's execution."""
    sig = trend_ensemble(df, threshold=THRESHOLD)   # agreement fraction, 0 below gate
    frac = sig.shift(1).fillna(0.0)                  # executed conviction, in [0, 1]
    gate = (frac > 0).astype(float)                  # 1 when long, 0 when flat

    # Raw fraction (kept even below the gate) -> long/short direction after warmup.
    raw = _trend_votes(df).mean(axis=1)
    warmup = df["close"].rolling(200, min_periods=200).mean().notna()
    raw_exec = raw.where(warmup).shift(1)
    direction = pd.Series(0.0, index=df.index)       # 0 = flat before warmup
    direction[raw_exec >= THRESHOLD] = 1.0           # long when votes agree
    direction[raw_exec < THRESHOLD] = -1.0           # short when they don't

    ret = df["close"].pct_change()
    realized = (rolling_vol(ret, 30) * np.sqrt(BPY)).shift(1)  # trailing ann. vol, causal
    voltarget = (VT_TARGET / realized).clip(0.0, cap).fillna(0.0)

    c = f"{cap:g}x"
    return {
        f"LF constant  {c}": gate * cap,                # long/flat, always cap when long
        f"LF voltarget {c}": voltarget * gate,          # long/flat, risk-sized to cap
        f"LS constant  {c}": direction * cap,           # long/short, always cap
        f"LS voltarget {c}": voltarget * direction,     # long/short, risk-sized to cap
    }


def show(ret, tag, trades=None, liq=None, df=None):
    s = M.summary(ret, BPY)
    extra = f"  trades={trades:>3}" if trades is not None else ""
    print(f"{tag:<18} ret={s['total_return']*100:>10.1f}%  cagr={s['cagr']*100:>6.1f}%  "
          f"sharpe={s['sharpe']:>5.2f}  sortino={s['sortino']:>5.2f}  "
          f"maxDD={s['max_drawdown']*100:>6.1f}%  calmar={s['calmar']:>5.2f}{extra}")
    if liq is not None and df is not None:
        print(f"    ^ LIQUIDATED on {df.index[liq].date()} -> account to zero, dead thereafter")
    return s


def main():
    df = fetch_klines("BTCUSDT", "1d", "2017-08-01")
    df = df[~df.index.duplicated()].sort_index().loc[START:]
    underlying = df["close"].pct_change().fillna(0.0)
    low_ret = (df["low"] / df["close"].shift(1) - 1).fillna(0.0)    # long kill: intraday low
    high_ret = (df["high"] / df["close"].shift(1) - 1).fillna(0.0)  # short kill: intraday high

    print(f"\nBTC/USDT leveraged FUTURES (caps {', '.join(f'{c:g}x' for c in CAPS)}) | "
          f"{df.index[0].date()} -> {df.index[-1].date()}  ({len(df)} daily bars)")
    print("Same signals/threshold/dates/costs as locked spot. Model adds funding "
          f"(~11.7%/yr) + intraday liquidation. Vol target = {VT_TARGET:g}.\n")

    show(underlying, "BTC buy & hold")
    print("-" * 118)

    results = {}
    for cap in CAPS:
        for tag, lev in build_leverage(df, cap).items():
            ret, liq = leveraged_returns(df, lev, underlying, low_ret, high_ret)
            results[tag] = (ret, liq, lev)
            show(ret, tag, trades=count_trades(lev), liq=liq, df=df)
        print("-" * 118)

    # ---- detail on the survivors (non-liquidated) --------------------------- #
    print("\n" + "=" * 118)
    print("DETAIL: drawdown profile, leverage usage, and yearly returns (survivors only)")
    print("=" * 118)
    for tag, (ret, liq, lev) in results.items():
        if liq is not None:
            continue
        eq = (1 + ret).cumprod()
        dd = eq / eq.cummax() - 1
        mag = lev.abs()[lev != 0]
        short_share = (lev < 0).sum() / (lev != 0).sum() if (lev != 0).any() else 0.0
        print(f"\n--- {tag.strip()} ---")
        print(f"  Max drawdown:         {dd.min()*100:6.1f}%   (on {dd.idxmin().date()})")
        print(f"  Days >50% underwater: {(dd < -0.50).sum():>4}     >70%: {(dd < -0.70).sum()}")
        print(f"  Leverage magnitude:   median {mag.median():.2f}x   "
              f"mean {mag.mean():.2f}x   max {mag.max():.2f}x   "
              f"(short {short_share*100:.0f}% of time in market)")
        yr = ret.groupby(ret.index.year).apply(lambda r: (1 + r).prod() - 1)
        print("  Year-by-year return:")
        for y, v in yr.items():
            bar = "#" * min(int(abs(v) * 20), 40)
            print(f"    {y}: {'+' if v >= 0 else '-'}{abs(v)*100:7.1f}%  {bar}")

    print("\nNote: constant high-leverage longs are expected to liquidate (BTC has many "
          ">20% intraday drops while trending up).")
    print("The spot 1x strategy remains the only version robustness-tested end to end.")


if __name__ == "__main__":
    main()
