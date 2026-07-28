"""Rigorous audit for the frozen BTCUSDT spot EMA(24,168) 4h candidate."""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)
BPY = 6 * 365
FAST, SLOW = 24, 168
BASE_COST = 0.0015
DATA_FILE = ROOT / "data" / "BTCUSDT_4h_2017-08-17_2026-07-27.parquet"
SCORE_START = pd.Timestamp("2019-01-01", tz="UTC")


def load_4h():
    """Load pre-2019 history so EMAs are warm before scoring starts."""
    return pd.read_parquet(DATA_FILE).sort_index()


def target(df: pd.DataFrame, fast=FAST, slow=SLOW) -> pd.Series:
    ef = df.close.ewm(span=fast, adjust=False).mean()
    es = df.close.ewm(span=slow, adjust=False).mean()
    out = (ef > es).astype(float)
    out.iloc[:slow] = 0.0
    return out


def simulate(df: pd.DataFrame, fast=FAST, slow=SLOW, cost=BASE_COST, delay=1):
    """Signal at close t; position changes at open t+delay."""
    desired = target(df, fast, slow)
    pos = desired.shift(delay).fillna(0.0)
    open_ret = df.open.shift(-1).div(df.open).sub(1)
    orders = pos.diff().fillna(pos)
    ret = pos * open_ret - orders.abs() * cost
    valid = open_ret.notna()
    return ret[valid], pos[valid], orders[valid]


def stats(ret: pd.Series, pos: pd.Series, orders: pd.Series) -> dict:
    eq = (1 + ret).cumprod()
    years = (ret.index[-1] - ret.index[0]).total_seconds() / (365.25 * 86400)
    dd = eq / eq.cummax() - 1
    gp, gl = ret[ret > 0].sum(), -ret[ret < 0].sum()
    return {
        "return_%": (eq.iloc[-1] - 1) * 100,
        "cagr_%": (eq.iloc[-1] ** (1 / years) - 1) * 100,
        "sharpe": ret.mean() / ret.std() * np.sqrt(BPY),
        "max_dd_%": dd.min() * 100,
        "profit_factor": gp / gl,
        "exposure_%": pos.mean() * 100,
        "entries": int((orders > 0.5).sum()),
        "exits": int((orders < -0.5).sum()),
        "orders": int((orders.abs() > 0.5).sum()),
    }


def buy_hold(df: pd.DataFrame):
    pos = pd.Series(1.0, index=df.index)
    pos.iloc[0] = 0
    open_ret = df.open.shift(-1).div(df.open).sub(1)
    orders = pos.diff().fillna(pos)
    ret = pos * open_ret - orders.abs() * BASE_COST
    valid = open_ret.notna()
    return ret[valid], pos[valid], orders[valid]


def cut(series, start=None, end=None):
    mask = pd.Series(True, index=series.index)
    if start:
        mask &= series.index >= pd.Timestamp(start, tz="UTC")
    if end:
        mask &= series.index < pd.Timestamp(end, tz="UTC")
    return series[mask]


def yearly_table(ret, pos, orders, bh_ret):
    rows = []
    for year, r in ret.groupby(ret.index.year):
        idx = r.index
        p, o, b = pos.loc[idx], orders.loc[idx], bh_ret.loc[idx]
        eq = (1+r).cumprod()
        beq = (1+b).cumprod()
        rows.append({
            "year": year,
            "strategy_%": (eq.iloc[-1]-1)*100,
            "buy_hold_%": (beq.iloc[-1]-1)*100,
            "edge_pp": ((eq.iloc[-1]-1)-(beq.iloc[-1]-1))*100,
            "max_dd_%": (eq/eq.cummax()-1).min()*100,
            "bh_max_dd_%": (beq/beq.cummax()-1).min()*100,
            "entries": int((o > .5).sum()),
            "exits": int((o < -.5).sum()),
            "orders": int((o.abs() > .5).sum()),
            "exposure_%": p.mean()*100,
        })
    return pd.DataFrame(rows)


def trade_spells(ret, pos, orders):
    rows = []
    entry = None
    spell_ret = []
    for ts in ret.index:
        if orders.loc[ts] > .5:
            entry, spell_ret = ts, []
        if pos.loc[ts] > .5 or (entry is not None and orders.loc[ts] < -.5):
            spell_ret.append(ret.loc[ts])
        if entry is not None and orders.loc[ts] < -.5:
            rows.append({
                "entry": entry, "exit": ts,
                "days": (ts-entry).total_seconds()/86400,
                "return_%": ((np.prod(1+np.asarray(spell_ret)))-1)*100,
            })
            entry, spell_ret = None, []
    return pd.DataFrame(rows)


def main():
    df = load_4h()
    ret, pos, orders = simulate(df)
    bhr, bhp, bho = buy_hold(df)
    ret, pos, orders = (
        ret.loc[SCORE_START:], pos.loc[SCORE_START:], orders.loc[SCORE_START:]
    )
    bhr, bhp, bho = (
        bhr.loc[SCORE_START:], bhp.loc[SCORE_START:], bho.loc[SCORE_START:]
    )

    full = pd.DataFrame([
        {"series": "EMA(24,168)", **stats(ret, pos, orders)},
        {"series": "buy_hold", **stats(bhr, bhp, bho)},
    ])
    yearly = yearly_table(ret, pos, orders, bhr)
    spells = trade_spells(ret, pos, orders)

    split_rows = []
    for name, start, end in [
        ("2019-2022", "2019-01-01", "2023-01-01"),
        ("2023-2024", "2023-01-01", "2025-01-01"),
        ("2025+", "2025-01-01", None),
        ("OOS split: 2023-07-15+", "2023-07-15", None),
    ]:
        for label, r, p, o in [
            ("strategy", ret, pos, orders), ("buy_hold", bhr, bhp, bho)
        ]:
            rr = cut(r, start, end)
            split_rows.append({
                "period": name, "series": label,
                **stats(rr, p.loc[rr.index], o.loc[rr.index])
            })
    splits = pd.DataFrame(split_rows)

    neighbors = []
    for fast in [12, 18, 24, 30, 36, 48]:
        for slow in [120, 144, 168, 192, 240]:
            if fast >= slow:
                continue
            r, p, o = simulate(df, fast, slow)
            r, p, o = r.loc[SCORE_START:], p.loc[SCORE_START:], o.loc[SCORE_START:]
            neighbors.append({"fast": fast, "slow": slow, **stats(r, p, o)})
    neighbors = pd.DataFrame(neighbors).sort_values("sharpe", ascending=False)

    stress = []
    for name, kwargs in [
        ("base", {"cost": .0015, "delay": 1}),
        ("cost 0.30%/side", {"cost": .0030, "delay": 1}),
        ("cost 0.45%/side", {"cost": .0045, "delay": 1}),
        ("cost 0.80%/side", {"cost": .0080, "delay": 1}),
        ("delay 2 bars", {"cost": .0015, "delay": 2}),
        ("delay 3 bars", {"cost": .0015, "delay": 3}),
        ("delay 6 bars", {"cost": .0015, "delay": 6}),
    ]:
        r, p, o = simulate(df, **kwargs)
        r, p, o = r.loc[SCORE_START:], p.loc[SCORE_START:], o.loc[SCORE_START:]
        stress.append({"scenario": name, **stats(r, p, o)})
    stress = pd.DataFrame(stress)

    # Bootstrap daily returns in 30-day blocks. This measures sampling uncertainty,
    # not structural/regime or exchange risk.
    daily = (1+ret).resample("1D").prod()-1
    rng = np.random.default_rng(20260728)
    block, paths = 30, 2000
    arr = daily.to_numpy()
    starts = np.arange(0, len(arr)-block+1)
    boot = []
    for _ in range(paths):
        sample = np.concatenate([
            arr[s:s+block] for s in rng.choice(starts, int(np.ceil(len(arr)/block)))
        ])[:len(arr)]
        eq = np.cumprod(1+sample)
        dd = np.min(eq/np.maximum.accumulate(eq)-1)
        sh = np.mean(sample)/np.std(sample)*np.sqrt(365) if np.std(sample) else 0
        cagr = eq[-1]**(365/len(sample))-1
        boot.append((cagr*100, sh, dd*100))
    boot = pd.DataFrame(boot, columns=["cagr_%", "sharpe", "max_dd_%"])
    boot_q = boot.quantile([.05, .5, .95]).rename_axis("quantile").reset_index()

    full.to_csv(REPORTS/"spot_4h_ema_full.csv", index=False)
    yearly.to_csv(REPORTS/"spot_4h_ema_yoy.csv", index=False)
    spells.to_csv(REPORTS/"spot_4h_ema_trades.csv", index=False)
    splits.to_csv(REPORTS/"spot_4h_ema_splits.csv", index=False)
    neighbors.to_csv(REPORTS/"spot_4h_ema_parameter_grid.csv", index=False)
    stress.to_csv(REPORTS/"spot_4h_ema_stress.csv", index=False)
    boot_q.to_csv(REPORTS/"spot_4h_ema_bootstrap.csv", index=False)

    print(f"DATA {df.index[0]} -> {df.index[-1]} ({len(df):,} four-hour bars)")
    print("\nFULL SAMPLE\n", full.round(2).to_string(index=False))
    print("\nYEAR BY YEAR\n", yearly.round(2).to_string(index=False))
    print("\nCHRONOLOGICAL SPLITS\n", splits.round(2).to_string(index=False))
    print("\nSTRESS\n", stress.round(2).to_string(index=False))
    print("\nPARAMETER GRID TOP 10\n", neighbors.head(10).round(2).to_string(index=False))
    print("\nBOOTSTRAP QUANTILES\n", boot_q.round(2).to_string(index=False))
    print("\nTRADE SPELLS\n", spells.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
