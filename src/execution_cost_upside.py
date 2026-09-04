"""Quantify the profit available from cheaper execution rather than more risk.

Signal tuning on this history is curve-fitting, and leverage buys return with
pure risk. Execution cost is the one lever that raises return without either.
Run: python src/execution_cost_upside.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies.spot_4h_dual_trend import config as SPOT  # noqa: E402
from strategies.ma250_4h_long_flat import config as PERP  # noqa: E402

DATA = ROOT / "data" / "BTCUSDT_4h_2017-08-17_2026-07-27.parquet"
BARS_PER_YEAR = 6 * 365
FUNDING_PER_BAR = 0.0001 / 2

# Bybit VIP 0 perpetual: taker 0.055%, maker 0.020%.
PERP_TAKER, PERP_MAKER = 0.00055, 0.00020
# Binance VIP 0 spot: taker 0.100%, maker 0.100%. BNB discount gives 0.075%.
SPOT_TAKER, SPOT_BNB = 0.0010, 0.00075


def spot_signal(close: pd.Series) -> pd.Series:
    ema_fast = close.ewm(span=SPOT.EMA_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=SPOT.EMA_SLOW, adjust=False).mean()
    momentum = close.pct_change(SPOT.MOMENTUM_LOOKBACK)
    regime = close.rolling(SPOT.TREND_SMA).mean()
    return ((ema_fast > ema_slow) & (momentum > 0) & (close > regime)).astype(float)


def run_spot(frame: pd.DataFrame, exposure: pd.Series, cost: float) -> float:
    held = exposure.shift(1).fillna(0.0)
    bar_return = frame["open"].pct_change().shift(-1).fillna(0.0)
    turnover = held.diff().abs().fillna(held.abs())
    net = held * bar_return - turnover * cost
    return ((1 + net).cumprod().iloc[-1] - 1) * 100


def run_perp(frame: pd.DataFrame, exposure: pd.Series, cost: float) -> float:
    open_ = frame["open"].to_numpy()
    want = exposure.shift(1).fillna(0.0).to_numpy()
    equity, held = 1.0, 0.0
    for i in range(len(frame) - 1):
        price, nxt = open_[i], open_[i + 1]
        if held != want[i]:
            equity -= abs(want[i] - held) * equity * cost
            held = want[i]
        if held:
            equity -= abs(held) * equity * FUNDING_PER_BAR
        equity *= 1 + held * (nxt - price) / price
    return (equity - 1) * 100


def main() -> None:
    frame = pd.read_parquet(DATA)
    close = frame["close"]

    print("=== Perpetual MA250 +2x: taker vs maker execution ===")
    long_signal = (close > close.rolling(PERP.MA_BARS).mean()).astype(float) * 2.0
    taker = run_perp(frame, long_signal, PERP_TAKER + PERP.SLIPPAGE)
    maker = run_perp(frame, long_signal, PERP_MAKER + PERP.SLIPPAGE)
    print(f"  taker 0.055% + slip : {taker:>12,.0f}%")
    print(f"  maker 0.020% + slip : {maker:>12,.0f}%")
    print(f"  uplift              : {(maker / taker - 1) * 100:>12,.1f}% more terminal profit")

    print("\n=== Spot dual trend: taker vs BNB-discounted fee ===")
    signal = spot_signal(close)
    base = run_spot(frame, signal, SPOT_TAKER + SPOT.SLIPPAGE)
    disc = run_spot(frame, signal, SPOT_BNB + SPOT.SLIPPAGE)
    print(f"  taker 0.100% + slip : {base:>12,.0f}%")
    print(f"  BNB   0.075% + slip : {disc:>12,.0f}%")
    print(f"  uplift              : {(disc / base - 1) * 100:>12,.1f}% more terminal profit")

    print("\n=== Vol-targeted challenger: taker vs maker ===")
    realised = close.pct_change().rolling(30).std() * np.sqrt(BARS_PER_YEAR)
    raw = ((close > close.rolling(PERP.MA_BARS).mean()).astype(float)
           * (0.50 / realised).clip(upper=1.5).fillna(0.0))
    held, banded = 0.0, []
    for want in raw.to_numpy():
        if abs(want - held) > 0.20 or want == 0.0 or held == 0.0:
            held = want
        banded.append(held)
    banded = pd.Series(banded, index=frame.index)
    vt_taker = run_perp(frame, banded, PERP_TAKER + PERP.SLIPPAGE)
    vt_maker = run_perp(frame, banded, PERP_MAKER + PERP.SLIPPAGE)
    print(f"  taker 0.055% + slip : {vt_taker:>12,.0f}%")
    print(f"  maker 0.020% + slip : {vt_maker:>12,.0f}%")
    print(f"  uplift              : {(vt_maker / vt_taker - 1) * 100:>12,.1f}%")

    print("\n=== Cost sensitivity: terminal profit vs round-trip cost ===")
    frames = []
    for label, runner, exposure, costs in (
        ("perp_ma250_2x", run_perp, long_signal, np.arange(0.0002, 0.0011, 0.0001)),
        ("spot_dual_trend", run_spot, signal, np.arange(0.0005, 0.0021, 0.0002)),
    ):
        series = pd.Series(
            {f"{c * 100:.2f}%": runner(frame, exposure, c) for c in costs},
            name="return_pct",
        )
        print(f"\n{label}:")
        print(series.round(0).to_string())
        frames.append(series.rename(label).to_frame().assign(strategy=label))
    combined = pd.concat(frames)
    combined.round(2).to_csv(ROOT / "reports" / "execution_cost_upside.csv")
    print("\nwrote reports/execution_cost_upside.csv")


if __name__ == "__main__":
    main()
