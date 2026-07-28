"""Honest 15-minute BTCUSDT perpetual trend-breakout study.

Signals are formed at a bar close and executed at the next bar open. The model
uses open-to-open returns, charges costs on every position change, and applies
historical Binance funding. Parameters are selected on 2019-2022 only; 2023-24
is validation and 2025+ is a final untouched test.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

# CoinDCX-like taker cost: 5 bps fee + 18% GST, plus 3 bps modeled slippage.
COST_SIDE = 0.00050 * 1.18 + 0.00030
START_CAPITAL = 100_000.0


@dataclass(frozen=True)
class Config:
    entry: int
    exit_ema: int
    vol_floor: float
    side: str


def load() -> tuple[pd.DataFrame, pd.Series]:
    files = sorted(DATA.glob("BTCUSDT_PERP_15m_*.parquet"))
    if not files:
        raise FileNotFoundError("Run: python src/fetch_futures.py 15m")
    df = pd.read_parquet(files[-1]).sort_index()
    fund = pd.read_parquet(DATA / "BTCUSDT_funding.parquet")["fundingRate"]
    fund = fund.reindex(df.index, fill_value=0.0)
    return df, fund


def position(df: pd.DataFrame, cfg: Config) -> pd.Series:
    close = df["close"]
    prior_high = df["high"].rolling(cfg.entry).max().shift(1)
    prior_low = df["low"].rolling(cfg.entry).min().shift(1)
    ema = close.ewm(span=cfg.exit_ema, adjust=False).mean()
    # Annualized realized volatility; avoids dead, fee-dominated conditions.
    rv = close.pct_change().rolling(96).std() * np.sqrt(365 * 96)
    active = rv >= cfg.vol_floor

    raw = pd.Series(np.nan, index=df.index)
    raw[(close > prior_high) & active] = 1.0
    if cfg.side == "both":
        raw[(close < prior_low) & active] = -1.0
    # Close-based exits; execution remains next-open below.
    raw[(raw.ffill().eq(1)) & (close < ema)] = 0.0
    raw[(raw.ffill().eq(-1)) & (close > ema)] = 0.0
    return raw.ffill().fillna(0.0)


def returns(df: pd.DataFrame, fund: pd.Series, cfg: Config,
            cost_side: float = COST_SIDE) -> tuple[pd.Series, pd.Series]:
    target = position(df, cfg)
    # Signal at t close becomes the position at t+1 open.
    held = target.shift(1).fillna(0.0)
    open_ret = df["open"].shift(-1).div(df["open"]).sub(1).fillna(0.0)
    turnover = held.diff().abs().fillna(held.abs())
    # Positive funding: longs pay, shorts receive. Negative funding reverses it.
    net = held * open_ret - turnover * cost_side - held * fund
    return net, turnover


def metrics(r: pd.Series, turnover: pd.Series) -> dict[str, float]:
    r = r.dropna()
    eq = (1 + r).cumprod()
    dd = eq.div(eq.cummax()).sub(1)
    years = max((r.index[-1] - r.index[0]).total_seconds() / (365.25 * 86400), 1/365)
    monthly = (1 + r).resample("ME").prod().sub(1)
    trades = float(turnover.reindex(r.index).sum() / 2)
    gross_profit = r[r > 0].sum()
    gross_loss = -r[r < 0].sum()
    return {
        "net_%": (eq.iloc[-1] - 1) * 100,
        "cagr_%": (eq.iloc[-1] ** (1 / years) - 1) * 100,
        "sharpe": r.mean() / r.std() * np.sqrt(365 * 96) if r.std() else 0,
        "max_dd_%": dd.min() * 100,
        "profit_factor": gross_profit / gross_loss if gross_loss else np.inf,
        "trades": trades,
        "positive_month_%": (monthly > 0).mean() * 100,
        "months": float(len(monthly)),
    }


def slice_result(df: pd.DataFrame, fund: pd.Series, cfg: Config,
                 start: str, end: str | None, cost=COST_SIDE):
    r, t = returns(df, fund, cfg, cost)
    mask = r.index >= pd.Timestamp(start, tz="UTC")
    if end:
        mask &= r.index < pd.Timestamp(end, tz="UTC")
    return r[mask], t[mask]


def main() -> None:
    df, fund = load()
    configs = [
        Config(e, x, v, s)
        for e, x, v, s in product(
            [16, 32, 48, 64, 96], [16, 32, 48, 64, 96],
            [0.30, 0.45, 0.60], ["long", "both"]
        )
        if x <= e * 2
    ]
    rows = []
    for cfg in configs:
        r, t = slice_result(df, fund, cfg, "2019-09-08", "2023-01-01")
        m = metrics(r, t)
        # Require activity and prefer risk-adjusted, not maximum-return, selection.
        score = m["sharpe"] + 0.01 * m["max_dd_%"] if m["trades"] >= 100 else -999
        rows.append({**cfg.__dict__, **m, "selection_score": score})
    search = pd.DataFrame(rows).sort_values("selection_score", ascending=False)
    search.to_csv(REPORTS / "btc_15m_train_grid.csv", index=False)
    best_row = search.iloc[0]
    best = Config(
        int(best_row.entry), int(best_row.exit_ema),
        float(best_row.vol_floor), str(best_row.side)
    )

    periods = [
        ("TRAIN 2019-22", "2019-09-08", "2023-01-01"),
        ("VALID 2023-24", "2023-01-01", "2025-01-01"),
        ("TEST 2025+", "2025-01-01", None),
        ("FULL", "2019-09-08", None),
    ]
    summary = []
    full_r = None
    for label, start, end in periods:
        r, t = slice_result(df, fund, best, start, end)
        summary.append({"period": label, **metrics(r, t)})
        if label == "FULL":
            full_r = r
    summary_df = pd.DataFrame(summary)
    summary_df.to_csv(REPORTS / "btc_15m_summary.csv", index=False)

    monthly = (1 + full_r).resample("ME").prod().sub(1)
    monthly.rename("return").to_csv(REPORTS / "btc_15m_monthly.csv")

    stress = []
    for bps in [5.9, 8.9, 12.0, 15.0]:
        r, t = slice_result(df, fund, best, "2025-01-01", None, bps / 10_000)
        stress.append({"cost_bps_side": bps, **metrics(r, t)})
    pd.DataFrame(stress).to_csv(REPORTS / "btc_15m_cost_stress.csv", index=False)

    print(f"DATA  {df.index[0]} -> {df.index[-1]}  ({len(df):,} bars)")
    print(f"BEST (train-selected only): {best}")
    print(f"COST  {COST_SIDE*10_000:.1f} bps/side incl fee GST + modeled slippage\n")
    print(summary_df.round(2).to_string(index=False))
    print("\nTEST COST STRESS")
    print(pd.DataFrame(stress).round(2).to_string(index=False))
    print("\nMONTHLY TEST RETURNS (%)")
    print((monthly.loc["2025-01-01":] * 100).round(2).to_string())
    print(f"\nRs {START_CAPITAL:,.0f} full-sample illustrative ending value: "
          f"Rs {START_CAPITAL * (1 + full_r).prod():,.0f}")


if __name__ == "__main__":
    main()
