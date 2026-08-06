"""Paper-only runner for the frozen 30/144/120/240 BTC spot challenger."""
from __future__ import annotations

import argparse
import hashlib
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
    require_new_bar,
)
from strategies.spot_4h_dual_trend_shadow import config as C  # noqa: E402

if not C.PAPER_ONLY or C.LIVE_TRADING_APPROVED:
    raise RuntimeError("shadow challenger must remain paper-only")

DEFAULT_RUNTIME = ROOT / "strategies" / C.SLUG / "runtime"
RUNTIME = Path(os.environ.get("PAPER_RUNTIME_DIR", DEFAULT_RUNTIME)).resolve()
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
                headers={"User-Agent": "btc-dual-trend-shadow-paper"},
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


def load_pinned_history() -> pd.DataFrame:
    path = ROOT / C.PINNED_HISTORY
    if not path.exists():
        raise RuntimeError(f"pinned signal history is missing: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != C.PINNED_HISTORY_SHA256:
        raise RuntimeError(
            "pinned signal history hash mismatch; refusing state mutation"
        )
    frame = pd.read_parquet(path).sort_index()
    if frame.empty or frame.index.tz is None:
        raise RuntimeError("pinned signal history is empty or not UTC-aware")
    return frame


def fetch_extension(
    first_bar: pd.Timestamp, now: pd.Timestamp
) -> tuple[pd.DataFrame, str]:
    cursor = int(first_bar.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    step_ms = int(TIMEFRAME.total_seconds() * 1000)
    frames = []
    sources = []
    while cursor <= end_ms:
        rows, source = get_json(
            "/api/v3/klines",
            {
                "symbol": C.SYMBOL,
                "interval": C.TIMEFRAME,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": C.BINANCE_PAGE_BARS,
            },
        )
        if not rows:
            break
        page = parse_klines(rows)
        frames.append(page)
        sources.append(source)
        next_cursor = int(page.index[-1].timestamp() * 1000) + step_ms
        if next_cursor <= cursor:
            raise RuntimeError("Binance pagination did not advance")
        cursor = next_cursor
        if len(rows) < C.BINANCE_PAGE_BARS:
            break
    if not frames:
        return pd.DataFrame(), ""
    return (
        pd.concat(frames).sort_index()[lambda x: ~x.index.duplicated(keep="last")],
        "+".join(dict.fromkeys(sources)),
    )


def market_data():
    now = pd.Timestamp.now("UTC")
    pinned = load_pinned_history()
    first_live_bar = pinned.index[-1] + TIMEFRAME
    extension, source = fetch_extension(first_live_bar, now)
    if extension.empty:
        raise RuntimeError("no Binance extension after pinned signal history")
    expected = pd.date_range(
        first_live_bar, extension.index[-1], freq=TIMEFRAME, tz="UTC"
    )
    if not extension.index.equals(expected):
        raise RuntimeError("Binance extension has a missing or unexpected four-hour bar")
    frame = pd.concat([pinned, extension]).sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    closed = frame[frame.index + TIMEFRAME <= now].copy()
    current = frame[frame.index + TIMEFRAME > now].copy()
    if len(closed) < C.MIN_WARMUP_BARS:
        raise RuntimeError(
            f"only {len(closed)} completed bars; need {C.MIN_WARMUP_BARS}"
        )
    if current.empty:
        raise RuntimeError("no current spot candle; refusing a historical opening fill")
    current_bar = current.iloc[-1]
    fill_latency = (
        now - current.index[-1]
    ).total_seconds() / 60
    return (
        closed,
        float(current_bar["close"]),
        float(current_bar["open"]),
        fill_latency,
        source,
        now,
    )


def signal_frame(closed: pd.DataFrame, config=C) -> pd.DataFrame:
    """Return causal indicators and target for every completed candle."""
    close = closed["close"]
    out = pd.DataFrame(index=closed.index)
    out["ema_fast"] = close.ewm(
        span=config.EMA_FAST, adjust=False
    ).mean()
    out["ema_slow"] = close.ewm(
        span=config.EMA_SLOW, adjust=False
    ).mean()
    out["momentum"] = close.pct_change(config.MOMENTUM_LOOKBACK)
    out["trend_sma"] = close.rolling(config.TREND_SMA).mean()
    out["ema_trend"] = out["ema_fast"] > out["ema_slow"]
    out["momentum_positive"] = out["momentum"] > 0
    out["sma_regime"] = close > out["trend_sma"]
    out["target"] = out[
        ["ema_trend", "momentum_positive", "sma_regime"]
    ].all(axis=1).astype("int8")
    out.iloc[
        :max(
            config.EMA_SLOW,
            config.MOMENTUM_LOOKBACK,
            config.TREND_SMA,
        ),
        out.columns.get_loc("target"),
    ] = 0
    return out


def signal(closed: pd.DataFrame, config=C) -> dict:
    """Compute the completed-candle signal; execution occurs in the next candle."""
    indicators = signal_frame(closed, config)
    latest = indicators.iloc[-1]
    conditions = {
        "ema_trend": bool(latest["ema_trend"]),
        "momentum_positive": bool(latest["momentum_positive"]),
        "sma_regime": bool(latest["sma_regime"]),
    }
    return {
        "bar_time": closed.index[-1],
        "closed_price": float(closed["close"].iloc[-1]),
        "ema_fast": float(latest["ema_fast"]),
        "ema_slow": float(latest["ema_slow"]),
        "momentum": float(latest["momentum"]),
        "sma": float(latest["trend_sma"]),
        "conditions": conditions,
        "filters_passed": sum(conditions.values()),
        "target": int(latest["target"]),
    }


def equity(state: dict, price: float) -> float:
    return state["cash"] + state["btc"] * price


def run(dry: bool = False) -> dict | None:
    closed, price, theoretical_open, fill_latency, source, now = market_data()
    sig = signal(closed)
    state = load_json(STATE, default_state())
    reconciled_bars = 0
    if state.get("last_bar") is not None:
        previous = pd.Timestamp(state["last_bar"])
        missing = closed.index[closed.index > previous]
        if len(missing) > 1:
            # We may safely fast-forward missed HOLD bars, but never manufacture a
            # historical fill. Any missed target change remains a hard failure.
            held_target = int(state["btc"] > 1e-12)
            missed_targets = signal_frame(closed).loc[missing[:-1], "target"]
            if (missed_targets != held_target).any():
                changed = missed_targets[missed_targets != held_target].index[0]
                raise RuntimeError(
                    "missed shadow trade at "
                    f"{changed}; refusing to invent a historical execution"
                )
            reconciled_bars = len(missing) - 1
            state["last_bar"] = str(missing[-2])
            state["last_target"] = held_target
            state["runs"] = state.get("runs", 0) + reconciled_bars
            print(
                f"[shadow-4h] reconciled {reconciled_bars} missed HOLD bars "
                f"through {missing[-2]}"
            )
    bar_status = require_new_bar(
        state.get("last_bar"), sig["bar_time"], TIMEFRAME, C.MAX_GAP_BARS
    )
    if bar_status == 0:
        print(f"[shadow-4h] already processed {sig['bar_time']}")
        return None

    eq_before = equity(state, price)
    if eq_before <= 0:
        raise RuntimeError("paper equity is non-positive; refusing state mutation")
    old_target = int(state["btc"] > 1e-12)
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

    # Floating-point dust must never turn a fully invested spot account negative.
    if abs(state["cash"]) < 1e-9:
        state["cash"] = 0.0
    if state["cash"] < 0 or state["btc"] < 0:
        raise RuntimeError("invalid spot balance; refusing state mutation")

    eq_after = equity(state, price)
    state["peak_equity"] = max(
        state.get("peak_equity", C.INITIAL_CAPITAL), eq_after
    )
    state["last_bar"] = str(sig["bar_time"])
    state["last_target"] = sig["target"]
    state["runs"] = state.get("runs", 0) + 1
    action = "ENTER" if traded_units > 0 else (
        "EXIT" if traded_units < 0 else "HOLD"
    )
    actual_exposure = state["btc"] * price / eq_after if eq_after else 0.0
    report = {
        "strategy": C.SLUG,
        "variant": C.VARIANT,
        "paper_only": True,
        "timestamp_utc": now.isoformat(),
        "closed_bar_time": sig["bar_time"].isoformat(),
        "closed_bar_date": str(sig["bar_time"]),
        "run_number": state["runs"],
        "dry_run": dry,
        "action": action,
        "side": "LONG" if state["btc"] else "FLAT",
        "data_source": source,
        "execution_source": f"{source}/ticker-in-current-kline",
        "theoretical_next_open_price": round(theoretical_open, 2),
        "fill_timestamp_utc": now.isoformat(),
        "fill_latency_minutes": round(fill_latency, 2),
        "btc_price": round(price, 2),
        "closed_price": round(sig["closed_price"], 2),
        "target_exposure": sig["target"],
        "actual_exposure": round(actual_exposure, 6),
        "ema_trend": sig["conditions"]["ema_trend"],
        "momentum_positive": sig["conditions"]["momentum_positive"],
        "sma_regime": sig["conditions"]["sma_regime"],
        "filters_passed": sig["filters_passed"],
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
        "total_return_pct": round(
            (eq_after / state["initial_capital"] - 1) * 100, 2
        ),
        "drawdown_pct": round(
            (eq_after / state["peak_equity"] - 1) * 100, 2
        ),
        "gap_bars_processed": reconciled_bars + bar_status,
    }
    report["summary"] = (
        f"{action} {'BTC' if sig['target'] else 'cash'}; "
        f"filters {sig['filters_passed']}/3; "
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
                "agreement": f"{sig['filters_passed']}/3",
                "previous_exposure_pct": old_target * 100,
                "new_target_exposure_pct": sig["target"] * 100,
                "current_exposure_pct": actual_exposure * 100,
                "btc_contract_qty": report["btc_units"],
            })
        except Exception as exc:
            print(f"[notify] {exc}")
    return report


def status() -> None:
    print(
        STATUS.read_text(encoding="utf-8")
        if STATUS.exists()
        else json.dumps(load_json(STATE, default_state()), indent=2)
    )


def reset() -> None:
    for path in (STATUS, TRADES):
        if path.exists():
            path.unlink()
    atomic_write_json(STATE, default_state())
    print("[shadow-4h] reset")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "status", "reset"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    {"run": lambda: run(args.dry_run), "status": status, "reset": reset}[
        args.command
    ]()


if __name__ == "__main__":
    main()
