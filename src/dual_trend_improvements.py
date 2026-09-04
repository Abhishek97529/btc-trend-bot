"""Test risk-reduction overlays for the four-hour spot dual-trend strategy.

Every variant is evaluated against the frozen baseline on identical data, costs,
and execution timing so differences come from the rule, not the harness.
Run: python src/dual_trend_improvements.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies.spot_4h_dual_trend import config as C  # noqa: E402

DATA = ROOT / "data" / "BTCUSDT_4h_2017-08-17_2026-07-27.parquet"
BARS_PER_YEAR = C.BARS_PER_YEAR
COST = C.FEE + C.SLIPPAGE


def load() -> pd.DataFrame:
    return pd.read_parquet(DATA)


def base_signal(close: pd.Series) -> pd.Series:
    """Frozen rule: EMA trend, positive momentum, and price above the regime SMA."""
    ema_fast = close.ewm(span=C.EMA_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=C.EMA_SLOW, adjust=False).mean()
    momentum = close.pct_change(C.MOMENTUM_LOOKBACK)
    regime = close.rolling(C.TREND_SMA).mean()
    raw = (ema_fast > ema_slow) & (momentum > 0) & (close > regime)
    return raw.astype(float)


def run(frame: pd.DataFrame, exposure: pd.Series, cost: float = COST) -> dict:
    """Execute `exposure` on the next bar's open and settle costs on turnover."""
    open_ = frame["open"]
    # Signal from a closed bar can only be executed on the following open.
    held = exposure.shift(1).fillna(0.0)
    bar_return = open_.pct_change().shift(-1).fillna(0.0)
    turnover = held.diff().abs().fillna(held.abs())
    net = held * bar_return - turnover * cost
    equity = (1 + net).cumprod()

    years = len(frame) / BARS_PER_YEAR
    total = equity.iloc[-1] - 1
    cagr = equity.iloc[-1] ** (1 / years) - 1
    drawdown = equity / equity.cummax() - 1
    downside = net[net < 0].std()
    return {
        "return_pct": total * 100,
        "cagr_pct": cagr * 100,
        "sharpe": net.mean() / net.std() * np.sqrt(BARS_PER_YEAR) if net.std() else 0.0,
        "sortino": net.mean() / downside * np.sqrt(BARS_PER_YEAR) if downside else 0.0,
        "max_dd_pct": drawdown.min() * 100,
        "exposure_pct": held.mean() * 100,
        "trades": int((turnover > 1e-9).sum()),
        "equity": equity,
    }


def show(rows: dict[str, dict]) -> pd.DataFrame:
    table = pd.DataFrame(
        {name: {k: v for k, v in row.items() if k != "equity"}
         for name, row in rows.items()}
    ).T
    table["calmar"] = table["cagr_pct"] / table["max_dd_pct"].abs()
    return table.round(2)


def main() -> None:
    frame = load()
    close = frame["close"]
    signal = base_signal(close)
    results: dict[str, dict] = {}

    results["buy_and_hold"] = run(frame, pd.Series(1.0, index=frame.index), cost=0.0)
    results["baseline_frozen"] = run(frame, signal)

    # 1. Chandelier-style trailing stop on the highest close since entry.
    for mult in (2.5, 3.0, 4.0):
        atr = (frame["high"] - frame["low"]).rolling(14).mean()
        held, peak, out = 0.0, 0.0, []
        for i in range(len(frame)):
            price = close.iloc[i]
            if held and price > peak:
                peak = price
            stop = peak - mult * (atr.iloc[i] if not np.isnan(atr.iloc[i]) else 0.0)
            if held and price < stop:
                held = 0.0
            elif signal.iloc[i] and not held:
                held, peak = 1.0, price
            elif not signal.iloc[i]:
                held = 0.0
            out.append(held)
        results[f"trailing_atr_{mult}x"] = run(frame, pd.Series(out, index=frame.index))

    # 2. Volatility targeting: scale exposure toward a constant risk budget.
    realised = close.pct_change().rolling(30).std() * np.sqrt(BARS_PER_YEAR)
    for target in (0.40, 0.50, 0.60):
        scale = (target / realised).clip(upper=1.0).fillna(0.0)
        results[f"voltarget_{int(target * 100)}"] = run(frame, signal * scale)

    # 3. Volatility targeting that is allowed modest leverage.
    for cap in (1.25, 1.5):
        scale = (0.50 / realised).clip(upper=cap).fillna(0.0)
        results[f"voltarget_50_cap{cap}"] = run(frame, signal * scale)

    # 3b. Banded volatility targeting: only resize on a material exposure change.
    # Naive vol targeting rebalances every bar, which multiplies turnover ~14x.
    for band in (0.10, 0.20):
        scale = (0.50 / realised).clip(upper=1.0).fillna(0.0)
        raw = (signal * scale).to_numpy()
        held, out = 0.0, []
        for want in raw:
            if abs(want - held) > band or want == 0.0 or held == 0.0:
                held = want
            out.append(held)
        results[f"voltarget_50_band{int(band * 100)}"] = run(
            frame, pd.Series(out, index=frame.index)
        )

    # 4. Regime filter: stand aside when the long trend is falling.
    regime_slope = close.rolling(C.TREND_SMA).mean().diff(30) > 0
    results["regime_slope_filter"] = run(frame, signal * regime_slope.astype(float))

    # 5. Combined: the best risk overlay plus the slope filter.
    scale = (0.50 / realised).clip(upper=1.0).fillna(0.0)
    results["voltarget_50_plus_slope"] = run(
        frame, signal * scale * regime_slope.astype(float)
    )

    table = show(results)
    print(table.to_string())
    out_path = ROOT / "reports" / "dual_trend_improvements.csv"
    table.to_csv(out_path)
    print(f"\nwrote {out_path}")

    # Cost stress: turnover-heavy overlays must survive a worse venue.
    print("\n=== total return % under rising per-side cost ===")
    scale = (0.50 / realised).clip(upper=1.0).fillna(0.0)
    banded = []
    held = 0.0
    for want in (signal * scale).to_numpy():
        if abs(want - held) > 0.20 or want == 0.0 or held == 0.0:
            held = want
        banded.append(held)
    candidates = {
        "baseline_frozen": signal,
        "voltarget_50": signal * scale,
        "voltarget_50_band20": pd.Series(banded, index=frame.index),
        "regime_slope_filter": signal * regime_slope.astype(float),
    }
    stress = pd.DataFrame({
        f"{c * 100:.2f}%": {
            name: run(frame, exposure, cost=c)["return_pct"]
            for name, exposure in candidates.items()
        }
        for c in (0.0015, 0.0025, 0.0038, 0.0064)
    })
    print(stress.round(0).to_string())
    stress.round(2).to_csv(ROOT / "reports" / "dual_trend_cost_stress.csv")


if __name__ == "__main__":
    main()
