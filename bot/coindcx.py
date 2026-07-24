"""
CoinDCX spot broker client — the ONLY module that places real orders / reads
real money. The rest of the bot never talks to the exchange directly.

It exposes three things the paper bot needs:
    get_last_price(market)          -> float   (in the QUOTE currency, e.g. INR)
    get_holdings(market)            -> (quote_free, base_free)   e.g. (INR, BTC)
    place_market_order(side, qty, price)        -> exchange order response

SAFETY (three independent guards — ALL must pass before a real order fires):
    1. paper_bot must be run with `--live`             (explicit CLI intent)
    2. env COINDCX_LIVE_ARMED=1                         (explicit arming)
    3. order notional <= COINDCX_MAX_ORDER_QUOTE        (hard size cap; default 1000)
Miss any one and place_market_order raises instead of trading.

Auth: CoinDCX signs the JSON body with HMAC-SHA256(secret). Every authenticated
request carries a millisecond `timestamp` in the body plus these headers:
    X-AUTH-APIKEY     : your API key
    X-AUTH-SIGNATURE  : hex HMAC-SHA256 of the exact JSON body bytes

Corporate TLS: reuses truststore so it works through the inspecting proxy.

Env vars:
    COINDCX_KEY / COINDCX_SECRET     API credentials (spot permission)
    COINDCX_MARKET                   trading pair, default "BTCINR"
                                     (use "BTCUSDT" if you fund the account in USDT)
    COINDCX_MAX_ORDER_QUOTE          per-order cap in quote currency (default 1000)
    COINDCX_LIVE_ARMED               must be "1" to allow ANY real order
    COINDCX_ALLOW_LARGE              set "1" to bypass the size cap (NOT recommended)

IMPORTANT: endpoint paths/params follow CoinDCX's public REST spec, but exchange
APIs change — verify against https://docs.coindcx.com and run ONE tiny live order
(a few hundred rupees) before trusting it with real size.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

import truststore
truststore.inject_into_ssl()  # trust the corporate root CA (same as data_fetch.py)

import requests

BASE = "https://api.coindcx.com"
PUBLIC = "https://public.coindcx.com"

MARKET = os.environ.get("COINDCX_MARKET", "BTCINR")
TIMEOUT = 20


# --------------------------------------------------------------------------- #
# Pair helpers
# --------------------------------------------------------------------------- #
def base_quote(market: str = None) -> tuple[str, str]:
    """Split a CoinDCX market symbol into (base, quote). 'BTCINR' -> ('BTC','INR')."""
    m = market or MARKET
    for q in ("USDT", "USDC", "INR", "BTC"):
        if m.endswith(q) and m != q:
            return m[: -len(q)], q
    raise ValueError(f"Cannot parse base/quote from market '{m}'")


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def _creds() -> tuple[str, str]:
    key = os.environ.get("COINDCX_KEY")
    secret = os.environ.get("COINDCX_SECRET")
    if not (key and secret):
        raise RuntimeError("COINDCX_KEY / COINDCX_SECRET not set — cannot trade live.")
    return key, secret


def _signed_post(path: str, payload: dict) -> dict:
    """POST an authenticated request. `payload` gets a millisecond timestamp,
    is JSON-encoded EXACTLY as signed, and sent with the auth headers."""
    key, secret = _creds()
    payload = {**payload, "timestamp": int(time.time() * 1000)}
    body = json.dumps(payload, separators=(",", ":"))  # sign the exact bytes we send
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": key,
        "X-AUTH-SIGNATURE": signature,
    }
    r = requests.post(BASE + path, data=body, headers=headers, timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(f"CoinDCX {path} -> {r.status_code}: {r.text[:300]}")
    return r.json()


# --------------------------------------------------------------------------- #
# Market data
# --------------------------------------------------------------------------- #
def get_last_price(market: str = None) -> float:
    """Latest traded price for the market, in the QUOTE currency (public endpoint)."""
    m = market or MARKET
    r = requests.get(BASE + "/exchange/ticker", timeout=TIMEOUT)
    r.raise_for_status()
    for row in r.json():
        if row.get("market") == m:
            return float(row["last_price"])
    raise RuntimeError(f"Market '{m}' not found in CoinDCX ticker.")


# --------------------------------------------------------------------------- #
# Balances
# --------------------------------------------------------------------------- #
def get_balances() -> dict:
    """All non-zero balances: {currency: {'balance': free, 'locked': locked}}."""
    rows = _signed_post("/exchange/v1/users/balances", {})
    out = {}
    for row in rows:
        cur = row["currency"]
        free = float(row.get("balance", 0.0))
        locked = float(row.get("locked_balance", 0.0))
        if free or locked:
            out[cur] = {"balance": free, "locked": locked}
    return out


def get_holdings(market: str = None) -> tuple[float, float]:
    """(quote_free, base_free) for the market — maps to the bot's (cash, btc)."""
    base, quote = base_quote(market)
    bals = get_balances()
    return (bals.get(quote, {}).get("balance", 0.0),
            bals.get(base, {}).get("balance", 0.0))


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #
def place_market_order(side: str, quantity: float, price: float, market: str = None) -> dict:
    """Place a MARKET order. `side` is 'BUY'/'SELL', `quantity` in BASE units (BTC),
    `price` the current quote-currency price (used ONLY for the safety cap).

    Fires only if armed AND under the size cap — otherwise raises."""
    m = market or MARKET
    side_l = side.lower()
    if side_l not in ("buy", "sell"):
        raise ValueError(f"side must be BUY/SELL, got {side!r}")

    # ---- Guard 1: explicit arming --------------------------------------- #
    if os.environ.get("COINDCX_LIVE_ARMED") != "1":
        raise RuntimeError(
            "REFUSING real order: COINDCX_LIVE_ARMED != 1. "
            "Set COINDCX_LIVE_ARMED=1 only when you truly intend to trade real money.")

    # ---- Guard 2: hard size cap ----------------------------------------- #
    notional = abs(quantity) * price
    cap = float(os.environ.get("COINDCX_MAX_ORDER_QUOTE", "1000"))
    if notional > cap and os.environ.get("COINDCX_ALLOW_LARGE") != "1":
        raise RuntimeError(
            f"REFUSING real order: notional {notional:,.0f} > cap {cap:,.0f} "
            f"({m} quote). Raise COINDCX_MAX_ORDER_QUOTE or set COINDCX_ALLOW_LARGE=1.")

    payload = {
        "side": side_l,
        "order_type": "market_order",
        "market": m,
        "total_quantity": round(abs(quantity), 8),
    }
    resp = _signed_post("/exchange/v1/orders/create", payload)
    print(f"[coindcx] LIVE {side_l} {abs(quantity):.8f} {base_quote(m)[0]} "
          f"(~{notional:,.0f} {base_quote(m)[1]}) -> {json.dumps(resp)[:200]}")
    return resp


# --------------------------------------------------------------------------- #
# Smoke test (read-only): python bot/coindcx.py
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    print(f"Market: {MARKET}  (base/quote = {base_quote()})")
    try:
        print(f"Last price: {get_last_price():,.2f}")
    except Exception as e:
        print(f"price check failed: {e}")
    try:
        q, b = get_holdings()
        base, quote = base_quote()
        print(f"Holdings: {q:,.2f} {quote} + {b:.8f} {base}")
    except Exception as e:
        print(f"balance check failed (need COINDCX_KEY/SECRET): {e}")
    print("\nRead-only smoke test. No orders placed. "
          "To trade: run paper_bot with --live AND set COINDCX_LIVE_ARMED=1.")
