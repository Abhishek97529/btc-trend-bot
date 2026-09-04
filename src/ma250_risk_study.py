"""Test risk-reduction overlays for the leveraged MA250 perpetual strategies.

The audit's central problem with these accounts is tail risk: ~42% of bootstrap
paths breached a 70% drawdown and a synthetic 50% adverse shock liquidated the
+2x book. This compares the frozen +2x rule against lower and volatility-scaled
leverage on identical data and costs.

Spot candles stand in for perpetual marks, matching the audit's extended
spot-proxy method. Funding is charged as a flat per-bar carry on notional.
Run: python src/ma250_risk_study.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies.ma250_4h_long_flat import config as C  # noqa: E402

DATA = ROOT / "data" / "BTCUSDT_4h_2017-08-17_2026-07-27.parquet"
BARS_PER_YEAR = 6 * 365
COST = C.FEE + C.SLIPPAGE
# Bybit BTCUSDT funding averages ~0.01% per 8h; 4h bars carry half of that.
FUNDING_PER_BAR = 0.0001 / 2
MAINTENANCE = C.MAINTENANCE_MARGIN


def run(frame: pd.DataFrame, exposure: pd.Series, cost: float = COST) -> dict:
    """Path-dependent leveraged equity with funding, costs, and liquidation."""
    open_ = frame["open"].to_numpy()
    low = frame["low"].to_numpy()
    high = frame["high"].to_numpy()
    want = exposure.shift(1).fillna(0.0).to_numpy()

    equity, held, curve, liquidated = 1.0, 0.0, [], False
    for i in range(len(frame) - 1):
        price, nxt = open_[i], open_[i + 1]
        if held != want[i]:
            equity -= abs(want[i] - held) * equity * cost
            held = want[i]
        if held:
            # Worst intrabar excursion decides survival before settlement.
            adverse = low[i] if held > 0 else high[i]
            move = (adverse - price) / price
            if equity * (1 + held * move) <= equity * abs(held) * MAINTENANCE:
                equity, held, liquidated = 0.0, 0.0, True
                curve.append(0.0)
                break
            equity -= abs(held) * equity * FUNDING_PER_BAR
        equity *= 1 + held * (nxt - price) / price
        curve.append(equity)

    series = pd.Series(curve, index=frame.index[:len(curve)])
    years = len(series) / BARS_PER_YEAR
    net = series.pct_change().fillna(0.0)
    drawdown = series / series.cummax() - 1
    downside = net[net < 0].std()
    turnover = pd.Series(want).diff().abs().fillna(0.0)
    return {
        "return_pct": (series.iloc[-1] - 1) * 100,
        "cagr_pct": (series.iloc[-1] ** (1 / years) - 1) * 100 if series.iloc[-1] > 0 else -100.0,
        "sharpe": net.mean() / net.std() * np.sqrt(BARS_PER_YEAR) if net.std() else 0.0,
        "sortino": net.mean() / downside * np.sqrt(BARS_PER_YEAR) if downside else 0.0,
        "max_dd_pct": drawdown.min() * 100,
        "trades": int((turnover > 1e-9).sum()),
        "liquidated": liquidated,
        "curve": series,
    }


def bootstrap_tail(frame: pd.DataFrame, exposure: pd.Series,
                   blocks: int = 300, block_bars: int = 42) -> dict:
    """Block-bootstrap the drawdown distribution; this is the audit's tail gate."""
    rng = np.random.default_rng(7)
    returns = frame["open"].pct_change().shift(-1).fillna(0.0).to_numpy()
    held = exposure.shift(1).fillna(0.0).to_numpy()
    n = len(returns)
    worst = []
    for _ in range(blocks):
        idx = np.concatenate([
            np.arange(s, min(s + block_bars, n))
            for s in rng.integers(0, n - block_bars, size=n // block_bars)
        ])
        path = 1 + held[idx] * returns[idx] - np.abs(np.diff(held[idx], prepend=0)) * COST
        path -= np.abs(held[idx]) * FUNDING_PER_BAR
        curve = np.cumprod(np.clip(path, 0, None))
        drawdown = curve / np.maximum.accumulate(curve) - 1
        worst.append(drawdown.min())
    worst = np.array(worst)
    return {
        "median_dd_pct": np.median(worst) * 100,
        "p05_dd_pct": np.percentile(worst, 5) * 100,
        "prob_dd_worse_70pct": float((worst < -0.70).mean() * 100),
    }


def main() -> None:
    frame = pd.read_parquet(DATA)
    close = frame["close"]
    average = close.rolling(C.MA_BARS).mean()
    long_signal = (close > average).astype(float)

    realised = close.pct_change().rolling(30).std() * np.sqrt(BARS_PER_YEAR)
    results, tails = {}, {}

    def band(series: pd.Series, width: float = 0.20) -> pd.Series:
        out, held = [], 0.0
        for want in series.to_numpy():
            if abs(want - held) > width or want == 0.0 or held == 0.0:
                held = want
            out.append(held)
        return pd.Series(out, index=series.index)

    candidates = {
        "spot_1x_hold": pd.Series(1.0, index=frame.index),
        "ma250_1x": long_signal,
        "ma250_1.5x": long_signal * 1.5,
        "ma250_2x_frozen": long_signal * 2.0,
        "ma250_voltgt60_cap2": band(
            long_signal * (0.60 / realised).clip(upper=2.0).fillna(0.0)),
        "ma250_voltgt50_cap2": band(
            long_signal * (0.50 / realised).clip(upper=2.0).fillna(0.0)),
        "ma250_voltgt50_cap1.5": band(
            long_signal * (0.50 / realised).clip(upper=1.5).fillna(0.0)),
    }

    for name, exposure in candidates.items():
        results[name] = run(frame, exposure)
        tails[name] = bootstrap_tail(frame, exposure)

    table = pd.DataFrame({
        name: {k: v for k, v in row.items() if k != "curve"}
        for name, row in results.items()
    }).T
    table["calmar"] = table["cagr_pct"] / table["max_dd_pct"].abs()
    tail_table = pd.DataFrame(tails).T
    combined = table.join(tail_table)
    print(combined.round(2).to_string())
    combined.round(2).to_csv(ROOT / "reports" / "ma250_risk_study.csv")

    print("\n=== survival under a synthetic adverse shock (at peak leverage) ===")
    for shock in (-0.30, -0.40, -0.50):
        row = {}
        for name, exposure in candidates.items():
            lev = float(exposure.abs().max())
            # Liquidation when the adverse move exhausts margin net of maintenance.
            row[name] = "LIQUIDATED" if 1 + lev * shock <= lev * MAINTENANCE else "survives"
        print(f"{shock:+.0%}: " + ", ".join(f"{k}={v}" for k, v in row.items()))

    # Sub-period check: the overlay must help in every regime, not just on average.
    print("\n=== per-period max drawdown %, frozen 2x vs vol-targeted ===")
    periods = {
        "2018_bear": ("2018-01-01", "2018-12-31"),
        "2019_2020": ("2019-01-01", "2020-12-31"),
        "2021_bull": ("2021-01-01", "2021-12-31"),
        "2022_bear": ("2022-01-01", "2022-12-31"),
        "2023_2024": ("2023-01-01", "2024-12-31"),
        "2025_on": ("2025-01-01", "2026-12-31"),
    }
    rows = {}
    for label, (start, end) in periods.items():
        window = frame.loc[start:end]
        if len(window) < 100:
            continue
        rows[label] = {
            name: run(window, exposure.loc[window.index])["max_dd_pct"]
            for name, exposure in candidates.items()
            if name in ("ma250_2x_frozen", "ma250_voltgt50_cap1.5", "ma250_voltgt50_cap2")
        }
    period_table = pd.DataFrame(rows).T.round(1)
    print(period_table.to_string())
    period_table.to_csv(ROOT / "reports" / "ma250_period_drawdowns.csv")

    print("\n=== per-period total return % ===")
    rows = {}
    for label, (start, end) in periods.items():
        window = frame.loc[start:end]
        if len(window) < 100:
            continue
        rows[label] = {
            name: run(window, exposure.loc[window.index])["return_pct"]
            for name, exposure in candidates.items()
            if name in ("ma250_2x_frozen", "ma250_voltgt50_cap1.5", "ma250_voltgt50_cap2")
        }
    return_table = pd.DataFrame(rows).T.round(1)
    print(return_table.to_string())
    return_table.to_csv(ROOT / "reports" / "ma250_period_returns.csv")


if __name__ == "__main__":
    main()
