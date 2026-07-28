"""BTC 15-minute session strategy study: ORB and VWAP mean reversion.

All decisions use completed candles and become positions at the next open.
Every crypto "session" is a fixed 24-hour UTC window; several UTC anchors are
tested on training data only. Positions are closed before the session boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)
COST_SIDE = 0.00050 * 1.18 + 0.00030
SPLITS = [
    ("TRAIN 2019-22", "2019-09-08", "2023-01-01"),
    ("VALID 2023-24", "2023-01-01", "2025-01-01"),
    ("TEST 2025+", "2025-01-01", None),
    ("FULL", "2019-09-08", None),
]


@dataclass(frozen=True)
class ORB:
    anchor: int
    opening: int
    buffer_atr: float
    max_hold: int
    side: str


@dataclass(frozen=True)
class MR:
    anchor: int
    opening: int
    z_entry: float
    max_hold: int
    side: str


def load():
    f = sorted(DATA.glob("BTCUSDT_PERP_15m_*.parquet"))[-1]
    df = pd.read_parquet(f).sort_index()
    fund = pd.read_parquet(DATA / "BTCUSDT_funding.parquet")["fundingRate"]
    return df, fund.reindex(df.index, fill_value=0.0)


def session_arrays(index: pd.DatetimeIndex, anchor: int):
    shifted = index - pd.Timedelta(hours=anchor)
    key = shifted.floor("D")
    bar = pd.Series(np.arange(len(index)), index=index).groupby(key).cumcount().to_numpy()
    return key, bar


def atr(df, n=32):
    pc = df.close.shift()
    tr = pd.concat([(df.high-df.low), (df.high-pc).abs(), (df.low-pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean().to_numpy()


def orb_position(df: pd.DataFrame, cfg: ORB) -> pd.Series:
    key, bar = session_arrays(df.index, cfg.anchor)
    ohi = df.high.where(bar < cfg.opening).groupby(key).transform("max").to_numpy()
    olo = df.low.where(bar < cfg.opening).groupby(key).transform("min").to_numpy()
    a = atr(df)
    c = df.close.to_numpy()
    direction = np.zeros(len(df))
    direction[(bar >= cfg.opening) & (c > ohi + cfg.buffer_atr*a)] = 1
    if cfg.side == "both":
        direction[(bar >= cfg.opening) & (c < olo - cfg.buffer_atr*a)] = -1
    entry_candidate = pd.Series(np.where(direction != 0, bar, np.nan), index=df.index)
    first_bar = entry_candidate.groupby(key).transform("min").to_numpy()
    first_dir = pd.Series(np.where(bar == first_bar, direction, np.nan), index=df.index)
    first_dir = first_dir.groupby(key).transform("max").fillna(0).to_numpy()
    active = (bar >= first_bar) & (bar < first_bar + cfg.max_hold) & (bar < 94)
    return pd.Series(np.where(active, first_dir, 0.0), index=df.index)


def mr_position(df: pd.DataFrame, cfg: MR) -> pd.Series:
    key, bar = session_arrays(df.index, cfg.anchor)
    typical = (df.high + df.low + df.close) / 3
    pv = typical * df.volume
    vwap = pv.groupby(key).cumsum() / df.volume.groupby(key).cumsum()
    dev = (df.close - vwap)
    # Expanding session dispersion computed from cumulative first/second moments.
    n = pd.Series(bar + 1, index=df.index)
    c1 = dev.groupby(key).cumsum()
    c2 = dev.pow(2).groupby(key).cumsum()
    scale = ((c2 - c1.pow(2)/n)/(n-1).clip(lower=1)).clip(lower=0).pow(.5)
    z = dev.div(scale).replace([np.inf, -np.inf], np.nan).to_numpy()
    # Each signal opens an equal-risk tranche for max_hold bars. Overlapping
    # tranches are averaged and total exposure is capped at one.
    impulse = np.zeros(len(df))
    eligible = (bar >= cfg.opening) & (bar < 94-cfg.max_hold)
    impulse[eligible & (z <= -cfg.z_entry)] = 1
    if cfg.side == "both":
        impulse[eligible & (z >= cfg.z_entry)] = -1
    tranches = pd.Series(impulse, index=df.index).groupby(key).rolling(
        cfg.max_hold, min_periods=1
    ).mean().reset_index(level=0, drop=True)
    return tranches.clip(-1, 1).where(bar < 94, 0)


def net_returns(df, fund, target, cost=COST_SIDE):
    held = target.shift(1).fillna(0)
    ret = df.open.shift(-1).div(df.open).sub(1).fillna(0)
    turn = held.diff().abs().fillna(held.abs())
    return held * ret - turn * cost - held * fund, turn


def metrics(r, turn):
    eq = (1+r).cumprod()
    dd = eq/eq.cummax()-1
    years = max((r.index[-1]-r.index[0]).total_seconds()/(365.25*86400), 1/365)
    mo = (1+r).resample("ME").prod()-1
    gp, gl = r[r>0].sum(), -r[r<0].sum()
    return {
        "net_%": (eq.iloc[-1]-1)*100,
        "cagr_%": (eq.iloc[-1]**(1/years)-1)*100,
        "sharpe": r.mean()/r.std()*np.sqrt(365*96) if r.std() else 0,
        "max_dd_%": dd.min()*100,
        "profit_factor": gp/gl if gl else np.inf,
        "trades": turn.sum()/2,
        "positive_month_%": (mo>0).mean()*100,
        "months": len(mo),
    }


def cut(s, start, end):
    m = s.index >= pd.Timestamp(start, tz="UTC")
    if end:
        m &= s.index < pd.Timestamp(end, tz="UTC")
    return s[m]


def evaluate_family(df, fund, family, configs):
    rows, cache = [], {}
    for cfg in configs:
        target = orb_position(df, cfg) if family == "ORB" else mr_position(df, cfg)
        r, t = net_returns(df, fund, target)
        cache[cfg] = (r, t)
        m = metrics(cut(r, "2019-09-08", "2023-01-01"), cut(t, "2019-09-08", "2023-01-01"))
        # A candidate must have enough observations. Max-DD penalty discourages lottery profiles.
        score = m["sharpe"] + 0.01*m["max_dd_%"] if m["trades"] >= 150 else -999
        rows.append({"family": family, **asdict(cfg), **m, "selection_score": score})
    grid = pd.DataFrame(rows).sort_values("selection_score", ascending=False)
    best_cfg = configs[grid.index[0]]
    r, t = cache[best_cfg]
    return grid, best_cfg, r, t


def main():
    df, fund = load()
    # Coarse, prespecified grid: midnight UTC and the U.S. cash-session vicinity.
    # Avoid testing hundreds of near-identical combinations.
    orb_cfg = [
        ORB(*x) for x in product([0, 13], [4], [0, .25, .5], [8, 16, 32], ["long", "both"])
    ]
    mr_cfg = [
        MR(*x) for x in product([0, 13], [8], [1.5, 2.0, 2.5, 3.0], [4, 8, 16], ["long", "both"])
    ]
    all_summary = []
    winners = []
    for family, configs in [("ORB", orb_cfg), ("VWAP_MR", mr_cfg)]:
        grid, cfg, r, t = evaluate_family(df, fund, family, configs)
        grid.to_csv(REPORTS/f"btc_15m_{family.lower()}_grid.csv", index=False)
        winners.append((family, cfg, r, t))
        for label, start, end in SPLITS:
            all_summary.append({
                "family": family, "config": str(cfg), "period": label,
                **metrics(cut(r, start, end), cut(t, start, end))
            })
        ((1+r).resample("ME").prod()-1).rename("return").to_csv(
            REPORTS/f"btc_15m_{family.lower()}_monthly.csv"
        )
    out = pd.DataFrame(all_summary)
    out.to_csv(REPORTS/"btc_15m_session_summary.csv", index=False)
    print(f"DATA {df.index[0]} -> {df.index[-1]} ({len(df):,} bars)")
    for family, cfg, _, _ in winners:
        print(f"{family} train-selected: {cfg}")
    print(f"COST {COST_SIDE*10_000:.1f} bps/side\n")
    print(out.round(2).to_string(index=False))
    print("\nUNTOUCHED TEST MONTHS")
    for family, cfg, r, _ in winners:
        mo = ((1+cut(r, "2025-01-01", None)).resample("ME").prod()-1)*100
        print(f"\n{family} {cfg}\n{mo.round(2).to_string()}")


if __name__ == "__main__":
    main()
