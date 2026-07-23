"""
Does the perpetual FUNDING RATE add edge to trend_ensemble? Measure it.

Thesis people trade: very high positive funding = crowded longs / overheated market
=> future returns are worse. So we test using funding as a RISK FILTER: cut or kill
long exposure when funding is in an extreme regime.

Funding posts every 8h on Binance futures. We aggregate to a daily mean and build a
rolling percentile, then test several overlay rules against the baseline.

Usage:  python src/test_funding.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import truststore
truststore.inject_into_ssl()
import requests
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_fetch import fetch_klines, DATA_DIR
from strategies_v2 import trend_ensemble
from backtest import run_backtest
import metrics as M

BPY = 365
FEE, SLIP = 0.001, 0.0005
FUND_URL = "https://fapi.binance.com/fapi/v1/fundingRate"


def fetch_funding(symbol="BTCUSDT") -> pd.Series:
    cache = DATA_DIR / f"{symbol}_funding.parquet"
    if cache.exists():
        return pd.read_parquet(cache)["fundingRate"]
    rows, cur = [], int(pd.Timestamp("2019-09-01", tz="UTC").timestamp() * 1000)
    end = int(pd.Timestamp.now("UTC").timestamp() * 1000)
    s = requests.Session()
    print("[funding] downloading ...")
    while cur < end:
        r = s.get(FUND_URL, params={"symbol": symbol, "startTime": cur, "limit": 1000}, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        cur = batch[-1]["fundingTime"] + 1
        time.sleep(0.15)
        if len(rows) % 5000 < 1000:
            print(f"[funding]  {len(rows)} rows @ {pd.to_datetime(batch[-1]['fundingTime'], unit='ms')}")
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["fundingRate"] = df["fundingRate"].astype(float)
    df = df.set_index("ts")[["fundingRate"]].sort_index()
    df.to_parquet(cache)
    print(f"[funding] saved {len(df)} funding prints")
    return df["fundingRate"]


def evalu(df, target, tag):
    res = run_backtest(df, target, fee=FEE, slippage=SLIP, bars_per_year=BPY)
    s = M.summary(res.returns, BPY, res.trades, res.gross_exposure_time)
    print(f"{tag:<30} ret={s['total_return']*100:8.1f}%  cagr={s['cagr']*100:6.1f}%  "
          f"sharpe={s['sharpe']:5.2f}  maxDD={s['max_drawdown']*100:6.1f}%  "
          f"calmar={s['calmar']:5.2f}  expo={s['exposure']*100:4.0f}%")
    return s


def main():
    df = fetch_klines("BTCUSDT", "1d", "2017-08-01")
    df = df[~df.index.duplicated()].sort_index()
    fund = fetch_funding()
    # daily mean funding, aligned to price index
    daily_fund = fund.resample("1D").mean()
    daily_fund.index = daily_fund.index.tz_convert("UTC")
    df = df.join(daily_fund.rename("funding"), how="left")
    # funding only exists from ~2019-09; restrict the test to where we have it
    df = df.loc[df["funding"].first_valid_index():].copy()
    df["funding"] = df["funding"].ffill()

    print(f"\nSample WITH funding: {df.index[0].date()} -> {df.index[-1].date()} ({len(df)} bars)")
    ann_fund = df["funding"].mean() * 3 * 365 * 100  # 3 prints/day
    print(f"Mean funding ~{ann_fund:.1f}%/yr paid by longs\n")

    base = trend_ensemble(df, threshold=0.5)
    print("== BASELINE (no funding filter) ====================================================")
    b = evalu(df, base, "no_filter")

    # rolling percentile of funding (causal)
    pct = df["funding"].rolling(90, min_periods=30).apply(
        lambda w: (w.iloc[-1] >= w).mean(), raw=False)

    print("\n== FILTER A: go flat when funding in top-decile 'overheated' regime ===============")
    for p in [0.80, 0.85, 0.90, 0.95]:
        overlay = base.where(pct < p, 0.0)
        evalu(df, overlay, f"flat if funding pct>{int(p*100)}")

    print("\n== FILTER B: HALVE exposure when funding overheated (soft) ========================")
    for p in [0.80, 0.85, 0.90]:
        overlay = base * np.where(pct >= p, 0.5, 1.0)
        evalu(df, pd.Series(overlay, index=df.index), f"half if funding pct>{int(p*100)}")

    print("\n== FILTER C: absolute funding threshold (>0.05%/8h ~ very hot) =====================")
    for thr in [0.0003, 0.0005, 0.0008]:
        overlay = base.where(df["funding"] < thr, 0.0)
        evalu(df, overlay, f"flat if funding>{thr*100:.2f}%")

    print(f"\nBaseline: sharpe {b['sharpe']:.2f}, return {b['total_return']*100:.0f}%, "
          f"maxDD {b['max_drawdown']*100:.1f}%")


if __name__ == "__main__":
    main()
