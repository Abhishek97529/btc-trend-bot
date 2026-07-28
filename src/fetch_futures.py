"""
Binance USD-M PERPETUAL futures data fetcher (fapi).

Downloads, for BTCUSDT PERP:
  * OHLCV klines            /fapi/v1/klines          (the tradeable perp price)
  * mark-price klines       /fapi/v1/markPriceKlines (what LIQUIDATIONS mark against)

We keep both because a leveraged backtest must check liquidation against the MARK
price, not the last-traded price. Funding is already cached separately
(data/BTCUSDT_funding.parquet).

Corporate TLS: inject the Windows cert store via truststore (proxy does TLS
inspection). Binance futures (fapi) is reachable locally; the 451 geo-block only
hit US-hosted CI runners.

Usage:  python src/fetch_futures.py            # 4h perp + mark, full history
        python src/fetch_futures.py 1h         # finer interval
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import truststore
truststore.inject_into_ssl()

import requests
import pandas as pd

FAPI = "https://fapi.binance.com"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}


def _interval_ms(interval: str) -> int:
    return int(interval[:-1]) * _MS[interval[-1]]


def _paginate(path: str, symbol: str, interval: str, start_ms: int, end_ms: int,
              ncols: int) -> list:
    step = _interval_ms(interval)
    rows, cur = [], start_ms
    session = requests.Session()
    while cur < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": cur, "endTime": end_ms, "limit": 1500}
        for attempt in range(4):
            try:
                r = session.get(FAPI + path, params=params, timeout=15)
                r.raise_for_status()
                break
            except Exception as e:
                if attempt == 3:
                    raise RuntimeError(f"{path} failed at {cur}: {e}")
                time.sleep(1.5)
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        cur = batch[-1][0] + step
        if len(rows) % 30000 < 1500:
            print(f"[fut]   {path.split('/')[-1]}: {len(rows):>7} bars @ "
                  f"{pd.to_datetime(batch[-1][0], unit='ms')}")
        time.sleep(0.12)
    return rows


def fetch_perp_ohlcv(symbol="BTCUSDT", interval="4h",
                     start="2019-09-08", end=None, force=False) -> pd.DataFrame:
    end = end or pd.Timestamp.now("UTC").strftime("%Y-%m-%d")
    cache = DATA_DIR / f"{symbol}_PERP_{interval}_{start}_{end}.parquet"
    if cache.exists() and not force:
        print(f"[fut] cached {cache.name}")
        return pd.read_parquet(cache)
    s = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    e = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    print(f"[fut] downloading PERP {symbol} {interval} {start}->{end}")
    rows = _paginate("/fapi/v1/klines", symbol, interval, s, e, 12)
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "trades", "tb_base", "tb_quote", "ignore"]
    df = pd.DataFrame(rows, columns=cols)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df = (df[["timestamp", "open", "high", "low", "close", "volume"]]
          .drop_duplicates("timestamp").set_index("timestamp").sort_index())
    df.to_parquet(cache)
    print(f"[fut] saved {len(df)} PERP bars -> {cache.name}")
    return df


def fetch_mark(symbol="BTCUSDT", interval="4h",
               start="2019-09-08", end=None, force=False) -> pd.DataFrame:
    end = end or pd.Timestamp.now("UTC").strftime("%Y-%m-%d")
    cache = DATA_DIR / f"{symbol}_MARK_{interval}_{start}_{end}.parquet"
    if cache.exists() and not force:
        print(f"[fut] cached {cache.name}")
        return pd.read_parquet(cache)
    s = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    e = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    print(f"[fut] downloading MARK {symbol} {interval} {start}->{end}")
    rows = _paginate("/fapi/v1/markPriceKlines", symbol, interval, s, e, 12)
    # markPriceKlines: [openTime, open, high, low, close, ignore, closeTime, ...]
    cols = ["open_time", "open", "high", "low", "close", "ig", "close_time",
            "a", "b", "c", "d", "e"][:len(rows[0])]
    df = pd.DataFrame(rows, columns=cols)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float)
    df = (df[["timestamp", "open", "high", "low", "close"]]
          .drop_duplicates("timestamp").set_index("timestamp").sort_index())
    df.columns = ["mark_open", "mark_high", "mark_low", "mark_close"]
    df.to_parquet(cache)
    print(f"[fut] saved {len(df)} MARK bars -> {cache.name}")
    return df


def fetch_funding(symbol="BTCUSDT", start="2019-09-08", end=None, force=False) -> pd.DataFrame:
    """Download complete USD-M perpetual funding history."""
    end = end or pd.Timestamp.now("UTC").strftime("%Y-%m-%d")
    cache = DATA_DIR / f"{symbol}_funding.parquet"
    if cache.exists() and not force:
        print(f"[fut] cached {cache.name}")
        return pd.read_parquet(cache)
    cur = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    rows = []
    session = requests.Session()
    while cur < end_ms:
        response = session.get(
            FAPI + "/fapi/v1/fundingRate",
            params={"symbol": symbol, "startTime": cur, "endTime": end_ms, "limit": 1000},
            timeout=15,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        rows.extend(batch)
        nxt = int(batch[-1]["fundingTime"]) + 1
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.12)
    frame = pd.DataFrame(rows)
    frame["ts"] = pd.to_datetime(frame["fundingTime"], unit="ms", utc=True)
    frame["fundingRate"] = frame["fundingRate"].astype(float)
    frame = frame[["ts", "fundingRate"]].drop_duplicates("ts").set_index("ts").sort_index()
    frame.to_parquet(cache)
    print(f"[fut] saved {len(frame)} funding events -> {cache.name}")
    return frame


if __name__ == "__main__":
    iv = sys.argv[1] if len(sys.argv) > 1 else "4h"
    ohlcv = fetch_perp_ohlcv(interval=iv, force="--force" in sys.argv)
    mark = fetch_mark(interval=iv, force="--force" in sys.argv)
    funding = fetch_funding(force="--force" in sys.argv)
    print(f"\nPERP  {len(ohlcv)} bars  {ohlcv.index[0]} -> {ohlcv.index[-1]}")
    print(f"MARK  {len(mark)} bars  {mark.index[0]} -> {mark.index[-1]}")
    print(ohlcv.tail(2))
    print(mark.tail(2))
    print(f"FUND  {len(funding)} events  {funding.index[0]} -> {funding.index[-1]}")
