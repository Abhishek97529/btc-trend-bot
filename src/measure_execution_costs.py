"""Measure real spread/slippage for the notional sizes the paper bots trade."""
import requests
import truststore

truststore.inject_into_ssl()

SIZES = [10_000, 20_000, 50_000]


def book(url, params):
    return requests.get(url, params=params, timeout=20,
                        headers={"User-Agent": "fee-study"}).json()


def sweep(levels, notional):
    """Volume-weighted fill price for a market order of `notional` USD."""
    spent = filled = 0.0
    for price, qty in ((float(p), float(q)) for p, q in levels):
        take = min(qty, (notional - spent) / price)
        spent += take * price
        filled += take
        if spent >= notional - 1e-9:
            break
    return spent / filled if filled else float("nan")


for label, url, params in (
    ("SPOT  BTCUSDT", "https://data-api.binance.vision/api/v3/depth",
     {"symbol": "BTCUSDT", "limit": 1000}),
    ("PERP  BTCUSDT", "https://api.bybit.com/v5/market/orderbook",
     {"category": "linear", "symbol": "BTCUSDT", "limit": 200}),
):
    data = book(url, params)
    if "result" in data:
        bids, asks = data["result"]["b"], data["result"]["a"]
    else:
        bids, asks = data["bids"], data["asks"]
    best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
    mid = (best_bid + best_ask) / 2
    print(f"\n{label}  mid={mid:,.2f}  "
          f"spread={(best_ask - best_bid) / mid * 1e4:.2f} bps")
    for size in SIZES:
        buy = sweep(asks, size)
        sell = sweep(bids, size)
        print(f"  ${size:>7,}  buy +{(buy / mid - 1) * 1e4:5.2f} bps   "
              f"sell {(sell / mid - 1) * 1e4:5.2f} bps")
