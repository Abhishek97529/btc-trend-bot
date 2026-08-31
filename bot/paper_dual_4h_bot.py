"""Paper-only runner for the frozen four-hour BTC spot dual-trend strategy."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

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
    reconcile_missed_holds,
    require_new_bar,
)
from strategies.spot_4h_dual_trend import config as C  # noqa: E402

RUNTIME = ROOT / "strategies" / "spot_4h_dual_trend" / "runtime"
STATE = RUNTIME / "state.json"
STATUS = RUNTIME / "status.json"
TRADES = RUNTIME / "trades.csv"
TIMEFRAME = pd.Timedelta(hours=4)
HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")


def default_state() -> dict:
    return {
        "cash": C.INITIAL_CAPITAL,
        "btc": 0.0,
        "initial_capital": C.INITIAL_CAPITAL,
        "peak_equity": C.INITIAL_CAPITAL,
        "last_bar": None,
        "last_target": 0,
        "runs": 0,
    }


def get_json(path: str, params: dict):
    errors = []
    for host in HOSTS:
        try:
            response = requests.get(
                host + path,
                params=params,
                timeout=15,
                headers={"User-Agent": "btc-dual-trend-paper"},
            )
            response.raise_for_status()
            return response.json(), host
        except Exception as exc:
            errors.append(f"{host}: {type(exc).__name__}")
    raise RuntimeError("Binance spot data unavailable: " + ", ".join(errors))


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
    rows, source = get_json(
        "/api/v3/klines",
        {"symbol": C.SYMBOL, "interval": "4h", "limit": 500},
    )
    frame = parse_klines(rows)
    now = pd.Timestamp.now("UTC")
    closed = frame[frame.index + TIMEFRAME <= now].copy()
    current = frame[frame.index + TIMEFRAME > now].copy()
    if len(closed) < C.TREND_SMA:
        raise RuntimeError(f"only {len(closed)} completed bars; need {C.TREND_SMA}")
    if current.empty:
        raise RuntimeError("no current spot candle; refusing a historical opening fill")
    return closed, float(current["close"].iloc[-1]), source, now


def signal_frame(closed: pd.DataFrame) -> pd.DataFrame:
    close = closed["close"]
    ema_fast = close.ewm(span=C.EMA_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=C.EMA_SLOW, adjust=False).mean()
    momentum = close.pct_change(C.MOMENTUM_LOOKBACK)
    average = close.rolling(C.TREND_SMA).mean()
    out = pd.DataFrame(index=closed.index)
    out["ema_fast"] = ema_fast
    out["ema_slow"] = ema_slow
    out["momentum"] = momentum
    out["trend_sma"] = average
    out["ema_trend"] = ema_fast > ema_slow
    out["momentum_positive"] = momentum > 0
    out["sma_regime"] = close > average
    out["target"] = (
        out["ema_trend"] & out["momentum_positive"] & out["sma_regime"]
    ).astype("int8")
    return out


def signal(closed: pd.DataFrame) -> dict:
    close = closed["close"]
    indicators = signal_frame(closed)
    latest = indicators.iloc[-1]
    conditions = {
        "ema_trend": bool(latest["ema_trend"]),
        "momentum": bool(latest["momentum_positive"]),
        "sma_regime": bool(latest["sma_regime"]),
    }
    return {
        "bar_time": closed.index[-1],
        "closed_price": float(close.iloc[-1]),
        "ema_fast": float(latest["ema_fast"]),
        "ema_slow": float(latest["ema_slow"]),
        "momentum": float(latest["momentum"]),
        "sma": float(latest["trend_sma"]),
        "conditions": conditions,
        "target": int(latest["target"]),
    }


def equity(state: dict, price: float) -> float:
    return state["cash"] + state["btc"] * price


def run(dry: bool = False) -> dict | None:
    closed, price, source, now = market_data()
    sig = signal(closed)
    state = load_json(STATE, default_state())
    late_recovery = os.getenv("PAPER_LATE_RECOVERY") == "1"
    old_target = int(state["btc"] > 1e-12)
    reconciled_bars, reconciled_through = reconcile_missed_holds(
        state.get("last_bar"),
        sig["bar_time"],
        signal_frame(closed)["target"],
        old_target,
        "dual-trend",
    )
    effective_last_bar = (
        str(reconciled_through) if reconciled_through is not None
        else state.get("last_bar")
    )
    bar_status = require_new_bar(
        effective_last_bar, sig["bar_time"], TIMEFRAME, C.MAX_GAP_BARS,
        allow_late_recovery=late_recovery,
    )
    if bar_status == 0:
        print(f"[dual-4h] already processed {sig['bar_time']}")
        return None

    before = dict(state)
    eq_before = equity(state, price)
    trade = sig["target"] != old_target
    rate = C.FEE + C.SLIPPAGE
    traded_units = 0.0
    cost = 0.0
    if trade and sig["target"] == 1:
        traded_units = state["cash"] / (price * (1 + rate))
        cost = traded_units * price * rate
        state["cash"] -= traded_units * price + cost
        state["btc"] += traded_units
    elif trade:
        traded_units = -state["btc"]
        proceeds = state["btc"] * price
        cost = proceeds * rate
        state["cash"] += proceeds - cost
        state["btc"] = 0.0

    eq_after = equity(state, price)
    state["peak_equity"] = max(state.get("peak_equity", C.INITIAL_CAPITAL), eq_after)
    state["last_bar"] = str(sig["bar_time"])
    state["last_target"] = sig["target"]
    state["runs"] = state.get("runs", 0) + reconciled_bars + 1
    action = "ENTER" if traded_units > 0 else ("EXIT" if traded_units < 0 else "HOLD")
    report = {
        "strategy": "spot_4h_dual_trend",
        "variant": "dual_trend",
        "timestamp_utc": now.isoformat(),
        "closed_bar_time": sig["bar_time"].isoformat(),
        "closed_bar_date": str(sig["bar_time"]),
        "run_number": state["runs"],
        "dry_run": dry,
        "action": action,
        "side": "LONG" if state["btc"] else "FLAT",
        "data_source": source,
        "execution_source": f"{source}/" + (
            "late-recovery-current-kline" if late_recovery and bar_status > 1
            else "ticker-in-current-kline"
        ),
        "btc_price": round(price, 2),
        "closed_price": round(sig["closed_price"], 2),
        "target_exposure": sig["target"],
        "actual_exposure": round(state["btc"] * price / eq_after, 6) if eq_after else 0.0,
        "conditions": sig["conditions"],
        "ema_fast": round(sig["ema_fast"], 2),
        "ema_slow": round(sig["ema_slow"], 2),
        "momentum": round(sig["momentum"], 6),
        "trend_sma": round(sig["sma"], 2),
        "btc_units_traded": round(traded_units, 8),
        "trade_value_usd": round(abs(traded_units) * price, 2),
        "cost_usd": round(cost, 2),
        "cash_usd": round(state["cash"], 2),
        "btc_units": round(state["btc"], 8),
        "total_equity_usd": round(eq_after, 2),
        "total_return_pct": round((eq_after / state["initial_capital"] - 1) * 100, 2),
        "drawdown_pct": round((eq_after / state["peak_equity"] - 1) * 100, 2),
        "gap_bars_processed": reconciled_bars + bar_status,
        "late_recovery": bool(late_recovery and bar_status > 1),
    }
    report["summary"] = (
        f"{action} {'BTC' if sig['target'] else 'cash'}; "
        f"conditions {sum(sig['conditions'].values())}/3; "
        f"BTC ${price:,.0f}; equity ${eq_after:,.2f}."
    )
    print(json.dumps(report, indent=2))

    if not dry:
        if trade:
            append_csv_dedup(TRADES, report, ("closed_bar_time", "action"))
        atomic_write_json(STATUS, report)
        atomic_write_json(STATE, state)
        try:
            notify({
                **report,
                "agreement": f"{sum(sig['conditions'].values())}/3",
                "previous_exposure_pct": old_target * 100,
                "new_target_exposure_pct": sig["target"] * 100,
                "current_exposure_pct": report["actual_exposure"] * 100,
                "btc_contract_qty": report["btc_units"],
            })
        except Exception as exc:
            print(f"[notify] {exc}")
    return report


def status() -> None:
    print(STATUS.read_text(encoding="utf-8") if STATUS.exists()
          else json.dumps(load_json(STATE, default_state()), indent=2))


def reset() -> None:
    for path in (STATUS, TRADES):
        if path.exists():
            path.unlink()
    atomic_write_json(STATE, default_state())
    print("[dual-4h] reset")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "status", "reset"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    {"run": lambda: run(args.dry_run), "status": status, "reset": reset}[args.command]()


if __name__ == "__main__":
    main()
