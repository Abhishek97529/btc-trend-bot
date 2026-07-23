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


def _download_binance(symbol, interval, start_ms, end_ms, step):
    """Paginate Binance klines. Rows are Binance's native list layout (_KLINE_COLS)."""
    rows = []
    cur = start_ms
    session = requests.Session()
    while cur < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": cur, "endTime": end_ms, "limit": 1000}
        r = None
        last_err = None
        # Fail FAST: short timeout, few attempts. If the runner can't reach Binance
        # (geo-block / TCP hang), we want to bail to the Coinbase fallback in seconds,
        # not stall for minutes on 30s socket timeouts.
        for attempt in range(2):
            for host in BINANCE_HOSTS:  # try each host before backing off
                try:
                    r = session.get(host + KLINES_PATH, params=params, timeout=8)
                    r.raise_for_status()
                    break
                except Exception as e:  # geo-block / transient network / rate limit
                    last_err = e
                    r = None
            if r is not None:
                break
            time.sleep(1)
        if r is None:
            raise RuntimeError(f"Binance request failed: {last_err}")
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        cur = batch[-1][0] + step
        if len(rows) % 20000 < 1000:
            print(f"[data]   {len(rows):>7} bars, at {pd.to_datetime(batch[-1][0], unit='ms')}")
        time.sleep(0.15)
    return rows


def _coinbase_product(symbol: str) -> str:
    """BTCUSDT -> BTC-USD (Coinbase prices in USD, close enough to USDT for signals)."""
    for q in ("USDT", "USDC", "USD"):
        if symbol.endswith(q):
            return f"{symbol[:-len(q)]}-USD"
    return symbol


def _download_coinbase(symbol, interval, start_ms, end_ms, step):
    """Fallback source (never geo-blocked on CI). Adapts Coinbase candles to _KLINE_COLS."""
    prod = _coinbase_product(symbol)
    gran = step // 1000  # seconds; Coinbase supports 60/300/900/3600/21600/86400
    url = f"https://api.exchange.coinbase.com/products/{prod}/candles"
    session = requests.Session()
    rows = []
    cur = start_ms // 1000
    end_s = end_ms // 1000
    print(f"[data]   coinbase fallback: {prod} granularity {gran}s")
    while cur < end_s:
        chunk_end = min(cur + 300 * gran, end_s)  # Coinbase caps at 300 candles/request
        params = {"granularity": gran,
                  "start": pd.Timestamp(cur, unit="s", tz="UTC").isoformat(),
                  "end": pd.Timestamp(chunk_end, unit="s", tz="UTC").isoformat()}
        r = session.get(url, params=params, timeout=30, headers={"User-Agent": "btc-trend-bot"})
        r.raise_for_status()
        for c in r.json():  # [time, low, high, open, close, volume]
            t, low, high, opn, close, vol = c[:6]
            # Binance layout: open_time, open, high, low, close, volume, +6 unused
            rows.append([t * 1000, opn, high, low, close, vol, 0, 0, 0, 0, 0, 0])
        cur = chunk_end
        time.sleep(0.3)
    return rows


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

    print(f"[data] downloading {symbol} {interval} from {start} to {end} ...")
    try:
        rows = _download_binance(symbol, interval, start_ms, end_ms, step)
        if not rows:
            raise RuntimeError("Binance returned no rows")
        print(f"[data]   source: Binance")
    except Exception as e:
        print(f"[data]   Binance unavailable ({e}); trying Coinbase fallback...")
        rows = _download_coinbase(symbol, interval, start_ms, end_ms, step)
        print(f"[data]   source: Coinbase")

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
