"""
Binance historical data fetcher.

Fetches OHLCV klines from Binance's public REST API (no API key required),
paginates through the full history, and caches the result to a local parquet/csv
file so we only download once.

Corporate TLS note: this machine sits behind a TLS-inspecting proxy, so we inject
the Windows certificate store into Python's SSL via `truststore`.
"""
from __future__ import annotations

import time
import os
from pathlib import Path

import truststore
truststore.inject_into_ssl()  # trust the corporate root CA via Windows cert store

import requests
import pandas as pd

KLINES_PATH = "/api/v3/klines"
# Try the public data-only mirror FIRST: it serves identical klines and is NOT
# geo-blocked, so it works from US-hosted CI runners (GitHub Actions). Fall back
# to the primary host (works locally / behind the corporate proxy).
BINANCE_HOSTS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
]
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# Binance kline column layout
_KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]


def _interval_ms(interval: str) -> int:
    unit = interval[-1]
    n = int(interval[:-1])
    mult = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit]
    return n * mult


def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    start: str = "2019-01-01",
    end: str | None = None,
    force: bool = False,
) -> pd.DataFrame:
    """Fetch (and cache) OHLCV klines. Returns a DataFrame indexed by UTC timestamp."""
    end = end or pd.Timestamp.now("UTC").strftime("%Y-%m-%d")
    cache = DATA_DIR / f"{symbol}_{interval}_{start}_{end}.parquet"
    if cache.exists() and not force:
        print(f"[data] loading cached {cache.name}")
        return pd.read_parquet(cache)

    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    step = _interval_ms(interval)

    rows = []
    cur = start_ms
    session = requests.Session()
    print(f"[data] downloading {symbol} {interval} from {start} to {end} ...")
    while cur < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cur,
            "endTime": end_ms,
            "limit": 1000,
        }
        r = None
        last_err = None
        for attempt in range(5):
            for host in BINANCE_HOSTS:  # try each host before backing off
                try:
                    r = session.get(host + KLINES_PATH, params=params, timeout=30)
                    r.raise_for_status()
                    break
                except Exception as e:  # geo-block / transient network / rate limit
                    last_err = e
                    r = None
            if r is not None:
                break
            wait = 2 ** attempt
            print(f"[data]   retry {attempt+1} after error: {last_err} (sleep {wait}s)")
            time.sleep(wait)
        if r is None:
            raise RuntimeError(f"Binance request failed after retries: {last_err}")

        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        last_open = batch[-1][0]
        cur = last_open + step
        # progress ping
        if len(rows) % 20000 < 1000:
            print(f"[data]   {len(rows):>7} bars, at {pd.to_datetime(last_open, unit='ms')}")
        time.sleep(0.15)  # be polite to the public endpoint

    df = pd.DataFrame(rows, columns=_KLINE_COLS)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    df["trades"] = df["trades"].astype(int)
    df = df[["timestamp", "open", "high", "low", "close", "volume", "quote_volume", "trades"]]
    df = df.drop_duplicates(subset="timestamp").set_index("timestamp").sort_index()

    df.to_parquet(cache)
    print(f"[data] saved {len(df)} bars -> {cache.name}")
    return df


if __name__ == "__main__":
    d = fetch_klines()
    print(d.head())
    print(d.tail())
    print(f"\n{len(d)} bars | {d.index[0]} -> {d.index[-1]}")
