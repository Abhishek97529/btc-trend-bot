"""Fail-closed paper runner for the two frozen MA250 perpetual strategies."""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import urlparse
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import truststore

truststore.inject_into_ssl()

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "bot")]

from bot.notify import notify  # noqa: E402
from bot.runtime import (  # noqa: E402
    append_csv_dedup,
    atomic_write_json,
    load_json,
    require_new_bar,
)

VARIANT = os.getenv("FIXED_4H_VARIANT", "").strip().lower()
if VARIANT == "long_flat":
    from strategies.ma250_4h_long_flat import config as C
elif VARIANT == "long_short":
    from strategies.ma250_4h_long_short import config as C
else:
    raise RuntimeError("FIXED_4H_VARIANT must be 'long_flat' or 'long_short'")

PACKAGE = ROOT / "strategies" / (
    "ma250_4h_long_flat" if VARIANT == "long_flat" else "ma250_4h_long_short"
)
RUNTIME = PACKAGE / "runtime"
STATE = RUNTIME / "state.json"
STATUS = RUNTIME / "status.json"
TRADES = RUNTIME / "trades.csv"
TIMEFRAME = pd.Timedelta(hours=4)
FAPI_ORIGIN = "https://fapi.binance.com"
FAPI_PROXY_URL = os.getenv("FAPI_PROXY_URL", "").strip().rstrip("/")


def default_state() -> dict:
    return {
        "wallet": C.INITIAL_CAPITAL,
        "qty": 0.0,
        "entry_price": 0.0,
        "initial_capital": C.INITIAL_CAPITAL,
        "peak_equity": C.INITIAL_CAPITAL,
        "last_bar": None,
        "last_direction": 0,
        "last_funding_time": None,
        "runs": 0,
        "liquidated": False,
    }


def get_json(url: str, params: dict):
    headers = {"User-Agent": "btc-4h-paper-suite"}
    request_url = url
    if FAPI_PROXY_URL and url.startswith(FAPI_ORIGIN + "/"):
        path = urlparse(url).path
        request_url = FAPI_PROXY_URL + "/market-data" + path
    response = requests.get(
        request_url,
        params=params,
        timeout=15,
        headers=headers,
    )
    response.raise_for_status()
    return response.json()


def parse_klines(rows) -> pd.DataFrame:
    columns = [
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote", "trades", "tb_base", "tb_quote", "ignore",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    frame["timestamp"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = frame[column].astype(float)
    return frame.set_index("timestamp")[
        ["open", "high", "low", "close", "volume"]
    ].sort_index()


def market_data():
    """Fetch perpetual trade and mark candles only; source substitution is forbidden."""
    now = pd.Timestamp.now("UTC")
    trade = parse_klines(get_json(
        "https://fapi.binance.com/fapi/v1/klines",
        {"symbol": C.SYMBOL, "interval": "4h", "limit": 500},
    ))
    mark = parse_klines(get_json(
        "https://fapi.binance.com/fapi/v1/markPriceKlines",
        {"symbol": C.SYMBOL, "interval": "4h", "limit": 500},
    ))
    for source, target in (
        ("open", "mark_open"),
        ("low", "mark_low"),
        ("high", "mark_high"),
        ("close", "mark_close"),
    ):
        trade[target] = mark[source].reindex(trade.index)
    if trade[["mark_open", "mark_low", "mark_high", "mark_close"]].tail(C.MA_BARS).isna().any().any():
        raise RuntimeError("perpetual mark candles are incomplete; refusing state mutation")

    closed = trade[trade.index + TIMEFRAME <= now].copy()
    current = trade[trade.index + TIMEFRAME > now].copy()
    if len(closed) < C.MA_BARS:
        raise RuntimeError(f"only {len(closed)} completed bars; need {C.MA_BARS}")
    if current.empty:
        raise RuntimeError("no current perpetual candle; refusing a stale historical fill")
    execution_price = float(current["close"].iloc[-1])
    return closed, current, execution_price, now


def funding_events(start_time: str | None, end_time: pd.Timestamp) -> list[dict]:
    if start_time is None:
        return []
    start = pd.Timestamp(start_time)
    rows = get_json(
        "https://fapi.binance.com/fapi/v1/fundingRate",
        {
            "symbol": C.SYMBOL,
            "startTime": int(start.timestamp() * 1000) + 1,
            "endTime": int(end_time.timestamp() * 1000),
            "limit": 1000,
        },
    )
    return [
        {
            "time": pd.to_datetime(int(row["fundingTime"]), unit="ms", utc=True),
            "rate": float(row["fundingRate"]),
        }
        for row in rows
    ]


def signal(closed: pd.DataFrame) -> dict:
    close = closed["close"]
    average = float(close.rolling(C.MA_BARS).mean().iloc[-1])
    direction = 1 if close.iloc[-1] > average else (-1 if C.SHORT_EXPOSURE else 0)
    target = C.LONG_EXPOSURE if direction > 0 else (
        -C.SHORT_EXPOSURE if direction < 0 else 0.0
    )
    return {
        "bar_time": closed.index[-1],
        "close": float(close.iloc[-1]),
        "sma250": average,
        "direction": direction,
        "target_exposure": target,
    }


def equity(state: dict, price: float) -> float:
    return state["wallet"] + state["qty"] * (price - state["entry_price"])


def apply_funding_and_liquidation(
    state: dict,
    closed: pd.DataFrame,
    current: pd.DataFrame,
    events: list[dict],
    now: pd.Timestamp,
) -> tuple[float, list[dict], bool]:
    if state["qty"] == 0 or state.get("last_bar") is None:
        return 0.0, [], False

    previous_bar = pd.Timestamp(state["last_bar"])
    risk_bars = pd.concat([
        closed[closed.index > previous_bar],
        current.iloc[[-1]],
    ]).sort_index()
    total_payment = 0.0
    applied = []
    for bar_time, bar in risk_bars.iterrows():
        bar_events = [
            event for event in events
            if bar_time <= event["time"] < min(bar_time + TIMEFRAME, now + pd.Timedelta(microseconds=1))
        ]
        for event in bar_events:
            payment = state["qty"] * float(bar["mark_open"]) * event["rate"]
            state["wallet"] -= payment
            total_payment += payment
            applied.append({**event, "payment": payment, "mark_price": float(bar["mark_open"])})

        adverse = float(bar["mark_low"] if state["qty"] > 0 else bar["mark_high"])
        adverse_equity = equity(state, adverse)
        maintenance = C.MAINTENANCE_MARGIN * abs(state["qty"]) * adverse
        if adverse_equity <= maintenance:
            state.update(wallet=0.0, qty=0.0, entry_price=0.0, liquidated=True)
            return total_payment, applied, True
    return total_payment, applied, False


def cost_aware_target_qty(target: float, eq_before: float, price: float,
                          old_qty: float) -> float:
    """Solve target notional against equity after changed-notional costs."""
    if target == 0 or eq_before <= 0:
        return 0.0
    rate = C.FEE + C.SLIPPAGE
    quantity = target * eq_before / price
    for _ in range(20):
        cost = abs(quantity - old_qty) * price * rate
        updated = target * max(eq_before - cost, 0.0) / price
        if abs(updated - quantity) < 1e-12:
            break
        quantity = updated
    return quantity


def load_state() -> dict:
    return load_json(STATE, default_state())


def run(dry: bool = False) -> dict | None:
    closed, current, price, now = market_data()
    sig = signal(closed)
    state = load_state()
    if state.get("liquidated"):
        raise RuntimeError("paper account is liquidated; archive and reset it explicitly")

    late_recovery = os.getenv("PAPER_LATE_RECOVERY") == "1"
    bar_status = require_new_bar(
        state.get("last_bar"), sig["bar_time"], TIMEFRAME, C.MAX_GAP_BARS,
        allow_late_recovery=late_recovery,
    )
    if bar_status == 0:
        print(f"[4h] already processed {sig['bar_time']}")
        return None

    before = dict(state)
    events = funding_events(state.get("last_funding_time"), now)
    funding_payment, applied_events, liquidated = apply_funding_and_liquidation(
        state, closed, current, events, now
    )

    old_qty = state["qty"]
    old_direction = int(np.sign(old_qty))
    direction_change = sig["direction"] != old_direction
    eq_before = max(equity(state, price), 0.0)
    target_qty = (
        cost_aware_target_qty(sig["target_exposure"], eq_before, price, old_qty)
        if direction_change and not liquidated else old_qty
    )
    changed_qty = target_qty - old_qty
    notional = abs(changed_qty) * price
    cost = notional * (C.FEE + C.SLIPPAGE) if direction_change and not liquidated else 0.0
    if direction_change and not liquidated:
        state["wallet"] = eq_before - cost
        state["qty"] = target_qty
        state["entry_price"] = price if target_qty else 0.0

    eq_after = max(equity(state, price), 0.0)
    state["peak_equity"] = max(state.get("peak_equity", C.INITIAL_CAPITAL), eq_after)
    state["last_bar"] = str(sig["bar_time"])
    state["last_direction"] = sig["direction"]
    state["last_funding_time"] = now.isoformat()
    state["runs"] = state.get("runs", 0) + 1

    action = (
        "LIQUIDATED" if liquidated else
        "EXIT" if direction_change and sig["direction"] == 0 else
        "REVERSE" if direction_change and old_qty != 0 else
        "ENTER" if direction_change else
        "HOLD"
    )
    actual_exposure = state["qty"] * price / eq_after if eq_after else 0.0
    report = {
        "strategy": C.SLUG,
        "variant": VARIANT,
        "timestamp_utc": now.isoformat(),
        "closed_bar_time": sig["bar_time"].isoformat(),
        "run_number": state["runs"],
        "dry_run": dry,
        "action": action,
        "side": "LONG" if state["qty"] > 0 else ("SHORT" if state["qty"] < 0 else "FLAT"),
        "data_source": (
            "bybit_linear_perpetual_and_mark_via_cloudflare"
            if FAPI_PROXY_URL else "binance_perpetual_and_mark"
        ),
        "funding_source": (
            "real_bybit" if FAPI_PROXY_URL else "real_binance"
        ),
        "btc_price": round(price, 2),
        "closed_price": round(sig["close"], 2),
        "sma250": round(sig["sma250"], 2),
        "target_exposure": round(sig["target_exposure"], 6),
        "actual_exposure": round(actual_exposure, 6),
        "previous_exposure": round(old_qty * price / eq_before, 6) if eq_before else 0.0,
        "btc_units_traded": round(changed_qty, 8),
        "trade_value_usd": round(notional, 2),
        "cost_usd": round(cost, 2),
        "funding_events": len(applied_events),
        "funding_rate_sum": sum(item["rate"] for item in applied_events),
        "funding_payment_usd": round(funding_payment, 4),
        "wallet_usd": round(state["wallet"], 2),
        "btc_contract_qty": round(state["qty"], 8),
        "entry_price": round(state["entry_price"], 2),
        "total_equity_usd": round(eq_after, 2),
        "total_return_pct": round((eq_after / state["initial_capital"] - 1) * 100, 2),
        "drawdown_pct": round((eq_after / state["peak_equity"] - 1) * 100, 2),
        "liquidated": state.get("liquidated", False),
        "gap_bars_processed": bar_status,
        "late_recovery": bool(late_recovery and bar_status > 1),
    }
    report["summary"] = (
        f"{action} {report['side']} target {sig['target_exposure']:+.2f}x "
        f"(actual {actual_exposure:+.3f}x); BTC ${price:,.0f}, "
        f"SMA250 ${sig['sma250']:,.0f}; equity ${eq_after:,.2f}."
    )
    print(json.dumps(report, indent=2))

    if not dry:
        if action != "HOLD" or applied_events:
            append_csv_dedup(TRADES, report, ("closed_bar_time", "action"))
        atomic_write_json(STATUS, report)
        atomic_write_json(STATE, state)
        try:
            compat = {
                **report,
                "closed_bar_date": str(sig["bar_time"]),
                "agreement": f"close {'>' if sig['direction'] > 0 else '<='} SMA250",
                "previous_exposure_pct": report["previous_exposure"] * 100,
                "new_target_exposure_pct": report["target_exposure"] * 100,
                "current_exposure_pct": report["actual_exposure"] * 100,
                "side": "BUY/LONG" if state["qty"] > 0 else (
                    "SELL/SHORT" if state["qty"] < 0 else "CASH/FLAT"
                ),
            }
            notify(compat)
        except Exception as exc:
            print(f"[notify] {exc}")
    return report


def status() -> None:
    print(STATUS.read_text(encoding="utf-8") if STATUS.exists()
          else json.dumps(load_state(), indent=2))


def reset() -> None:
    for path in (STATUS, TRADES):
        if path.exists():
            path.unlink()
    atomic_write_json(STATE, default_state())
    print(f"[4h] reset {VARIANT}")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "status", "reset"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    {"run": lambda: run(args.dry_run), "status": status, "reset": reset}[args.command]()


if __name__ == "__main__":
    main()
