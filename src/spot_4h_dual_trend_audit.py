"""Reproducible audit of the BTCUSDT spot 4h dual-trend candidate."""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spot_4h_dual_trend_config as C

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)
DATA_FILE = ROOT / "data" / "BTCUSDT_4h_2017-08-17_2026-07-27.parquet"
SCORE_START = pd.Timestamp("2019-01-01", tz="UTC")


def load_4h():
    """Load pre-2019 history so every indicator is warm at the score boundary."""
    return pd.read_parquet(DATA_FILE).sort_index()


def signal(df, fast=C.EMA_FAST, slow=C.EMA_SLOW,
           lookback=C.MOMENTUM_LOOKBACK, trend=C.TREND_SMA):
    ema_ok = (
        df.close.ewm(span=fast, adjust=False).mean()
        > df.close.ewm(span=slow, adjust=False).mean()
    )
    momentum_ok = df.close.pct_change(lookback) > 0
    trend_ok = df.close > df.close.rolling(trend).mean()
    out = (ema_ok & momentum_ok & trend_ok).astype(float)
    out.iloc[:max(slow, lookback, trend)] = 0
    return out


def simulate(df, fast=C.EMA_FAST, slow=C.EMA_SLOW,
             lookback=C.MOMENTUM_LOOKBACK, trend=C.TREND_SMA,
             cost=C.FEE+C.SLIPPAGE, delay=C.EXECUTION_DELAY_BARS):
    desired = signal(df, fast, slow, lookback, trend)
    position = desired.shift(delay).fillna(0)
    open_return = df.open.shift(-1).div(df.open).sub(1)
    change = position.diff().fillna(position)
    returns = position * open_return - change.abs() * cost
    valid = open_return.notna()
    return returns[valid], position[valid], change[valid]


def benchmark(df):
    position = pd.Series(1.0, index=df.index)
    position.iloc[0] = 0
    open_return = df.open.shift(-1).div(df.open).sub(1)
    change = position.diff().fillna(position)
    returns = position * open_return - change.abs() * (C.FEE+C.SLIPPAGE)
    valid = open_return.notna()
    return returns[valid], position[valid], change[valid]


def metrics(r, p, changes):
    equity = (1+r).cumprod()
    dd = equity/equity.cummax()-1
    years = (r.index[-1]-r.index[0]).total_seconds()/(365.25*86400)
    return {
        "return_%": (equity.iloc[-1]-1)*100,
        "cagr_%": (equity.iloc[-1]**(1/years)-1)*100,
        "sharpe": r.mean()/r.std()*np.sqrt(C.BARS_PER_YEAR),
        "max_dd_%": dd.min()*100,
        "exposure_%": p.mean()*100,
        "entries": int((changes > .5).sum()),
        "exits": int((changes < -.5).sum()),
        "orders": int((changes.abs() > .5).sum()),
    }


def subset(s, start=None, end=None):
    x = s
    if start:
        x = x[x.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        x = x[x.index < pd.Timestamp(end, tz="UTC")]
    return x


def main():
    df = load_4h()
    r, p, ch = simulate(df)
    br, bp, bch = benchmark(df)
    # Indicators use all available prehistory; reported performance begins in 2019.
    r, p, ch = r.loc[SCORE_START:], p.loc[SCORE_START:], ch.loc[SCORE_START:]
    br, bp, bch = br.loc[SCORE_START:], bp.loc[SCORE_START:], bch.loc[SCORE_START:]

    summary = pd.DataFrame([
        {"series": "dual_trend", **metrics(r, p, ch)},
        {"series": "buy_hold", **metrics(br, bp, bch)},
    ])

    yoy = []
    for year, yr in r.groupby(r.index.year):
        ix = yr.index
        eq = (1+yr).cumprod()
        beq = (1+br.loc[ix]).cumprod()
        yoy.append({
            "year": year,
            "strategy_%": (eq.iloc[-1]-1)*100,
            "buy_hold_%": (beq.iloc[-1]-1)*100,
            "edge_pp": ((eq.iloc[-1]-1)-(beq.iloc[-1]-1))*100,
            "max_dd_%": (eq/eq.cummax()-1).min()*100,
            "bh_max_dd_%": (beq/beq.cummax()-1).min()*100,
            "entries": int((ch.loc[ix]>.5).sum()),
            "exits": int((ch.loc[ix]<-.5).sum()),
            "orders": int((ch.loc[ix].abs()>.5).sum()),
            "exposure_%": p.loc[ix].mean()*100,
        })
    yoy = pd.DataFrame(yoy)

    periods = []
    for period, start, end in [
        ("2019-2022", "2019-01-01", "2023-01-01"),
        ("2023-2024", "2023-01-01", "2025-01-01"),
        ("2025+", "2025-01-01", None),
        ("2023-07-15+", "2023-07-15", None),
    ]:
        for name, rr, pp, cc in [
            ("strategy", r, p, ch), ("buy_hold", br, bp, bch)
        ]:
            sr = subset(rr, start, end)
            periods.append({
                "period": period, "series": name,
                **metrics(sr, pp.loc[sr.index], cc.loc[sr.index])
            })
    periods = pd.DataFrame(periods)

    stress = []
    for scenario, cost, delay in [
        ("base", .0015, 1),
        ("cost 0.30%/side", .0030, 1),
        ("cost 0.45%/side", .0045, 1),
        ("cost 0.80%/side", .0080, 1),
        ("delay 2 bars", .0015, 2),
        ("delay 3 bars", .0015, 3),
        ("delay 6 bars", .0015, 6),
    ]:
        sr, sp, sc = simulate(df, cost=cost, delay=delay)
        sr, sp, sc = sr.loc[SCORE_START:], sp.loc[SCORE_START:], sc.loc[SCORE_START:]
        stress.append({"scenario": scenario, **metrics(sr, sp, sc)})
    stress = pd.DataFrame(stress)

    grid = []
    for fast in [18, 24, 30]:
        for slow in [144, 168, 192]:
            for lookback in [90, 120, 150, 180]:
                for trend in [240, 300, 360]:
                    sr, sp, sc = simulate(df, fast, slow, lookback, trend)
                    sr, sp, sc = (
                        sr.loc[SCORE_START:], sp.loc[SCORE_START:], sc.loc[SCORE_START:]
                    )
                    grid.append({
                        "fast": fast, "slow": slow,
                        "lookback": lookback, "trend": trend,
                        **metrics(sr, sp, sc)
                    })
    grid = pd.DataFrame(grid).sort_values("sharpe", ascending=False)

    summary.to_csv(REPORTS/"spot_4h_dual_trend_summary.csv", index=False)
    yoy.to_csv(REPORTS/"spot_4h_dual_trend_yoy.csv", index=False)
    periods.to_csv(REPORTS/"spot_4h_dual_trend_periods.csv", index=False)
    stress.to_csv(REPORTS/"spot_4h_dual_trend_stress.csv", index=False)
    grid.to_csv(REPORTS/"spot_4h_dual_trend_grid.csv", index=False)

    print(f"DATA {df.index[0]} -> {df.index[-1]} ({len(df):,} bars)")
    print("\nSUMMARY\n", summary.round(2).to_string(index=False))
    print("\nYEAR BY YEAR\n", yoy.round(2).to_string(index=False))
    print("\nPERIODS\n", periods.round(2).to_string(index=False))
    print("\nSTRESS\n", stress.round(2).to_string(index=False))
    print("\nGRID QUANTILES\n", grid[[
        "return_%", "cagr_%", "sharpe", "max_dd_%"
    ]].quantile([0, .05, .5, .95, 1]).round(2).to_string())


if __name__ == "__main__":
    main()
