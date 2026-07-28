"""
Fetch intraday (1h) OHLC for US indices / ETFs from the Yahoo v8 chart API.

Corporate proxy does TLS inspection -> use an unverified SSL context (public price
data only). Yahoo caps 1h data at ~730 days regardless of the requested range, so
this is the LONGEST free hourly history available. We resample 1h -> 4h locally.

Usage:  python src/fetch_intraday_us.py
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.request
from pathlib import Path

import pandas as pd

CTX = ssl._create_unverified_context()
UA = {"User-Agent": "Mozilla/5.0"}
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "markets" / "us"


def fetch_1h(symbol: str, rng: str = "730d") -> pd.DataFrame:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval=1h&range={rng}&includePrePost=false")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
        j = json.load(r)
    res = j["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    idx = pd.to_datetime(ts, unit="s", utc=True)
    df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"],
                       "close": q["close"], "volume": q["volume"]}, index=idx).dropna()
    df.index.name = "ts"
    return df


def main():
    for sym, name in [("%5ENDX", "ndx"), ("QQQ", "qqq"), ("%5EGSPC", "spx")]:
        try:
            df = fetch_1h(sym)
            out = DATA_DIR / f"intr_{name}_1h.csv"
            df.to_csv(out)
            span = (df.index.max() - df.index.min()).days / 365.25
            print(f"{name:5s} {len(df):>6} 1h bars  {df.index.min().date()} -> "
                  f"{df.index.max().date()}  ({span:.2f}y)  -> {out}")
        except Exception as e:
            print(f"{name:5s} FAILED: {e}")
        time.sleep(1)


if __name__ == "__main__":
    main()
