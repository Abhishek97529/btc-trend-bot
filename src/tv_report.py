"""
TradingView-style Strategy Tester report for `trend_ensemble` on the DAILY chart.

Reproduces the metrics TradingView's Strategy Tester shows (Performance Summary +
Trades Analysis), plus year-by-year returns and rolling-return statistics.

Trade definition: the strategy sizes exposure fractionally (0..1). A "trade" here is
one contiguous in-market episode -- it opens when exposure goes from 0 to >0 and
closes when it returns to 0. The trade's return is the compounded net strategy
return over that episode (so partial re-sizing inside the episode is included).

Usage:  python src/tv_report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_fetch import fetch_klines
from strategies_v2 import trend_ensemble
from legacy_strategies import buy_and_hold
from backtest import run_backtest
import metrics as M

BPY = 365
FEE, SLIP = 0.001, 0.0005
INIT = 10_000.0
THRESHOLD = 0.5
REPORTS = Path(__file__).resolve().parent.parent / "reports"
REPORTS.mkdir(exist_ok=True)


def extract_trades(price: pd.Series, executed: pd.Series, net_ret: pd.Series,
                   equity: pd.Series) -> pd.DataFrame:
    """Split the backtest into contiguous in-market episodes (round-trip trades)."""
    in_mkt = (executed.abs() > 1e-9).values
    idx = executed.index
    trades = []
    i = 0
    n = len(in_mkt)
    while i < n:
        if in_mkt[i]:
            j = i
            while j + 1 < n and in_mkt[j + 1]:
                j += 1
            seg = net_ret.iloc[i:j + 1]
            tr_ret = float((1 + seg).prod() - 1)
            eq_before = float(equity.iloc[i - 1]) if i > 0 else INIT
            trades.append({
                "entry_date": idx[i].date(),
                "exit_date": idx[j].date(),
                "bars": j - i + 1,
                "entry_price": round(float(price.iloc[i]), 2),
                "exit_price": round(float(price.iloc[j]), 2),
                "return_pct": round(tr_ret * 100, 2),
                "pnl": round(eq_before * tr_ret, 2),
            })
            i = j + 1
        else:
            i += 1
    return pd.DataFrame(trades)


def tv_summary(df, sig):
    price = df["close"]
    res = run_backtest(df, sig, fee=FEE, slippage=SLIP, bars_per_year=BPY)
    net = res.returns
    equity = INIT * res.equity
    # buy & hold
    bh_ret = price.pct_change().fillna(0.0)
    bh_final = INIT * (1 + bh_ret).prod()

    # commission (approx, compounding $)
    dpos = res.position.diff().abs().fillna(res.position.abs())
    comm = float((dpos * (FEE + SLIP) * equity.shift(1).fillna(INIT)).sum())

    trades = extract_trades(price, res.position, net, equity)
    wins = trades[trades.pnl > 0]
    losses = trades[trades.pnl <= 0]
    gross_profit = wins.pnl.sum()
    gross_loss = losses.pnl.sum()
    final_eq = float(equity.iloc[-1])

    equity_curve = equity
    runup = float((equity_curve / equity_curve.cummin() - 1).max()) * 100

    s = {
        "Net Profit $": round(final_eq - INIT, 2),
        "Net Profit %": round((final_eq / INIT - 1) * 100, 2),
        "Buy&Hold Return %": round((bh_final / INIT - 1) * 100, 2),
        "Gross Profit $": round(gross_profit, 2),
        "Gross Loss $": round(gross_loss, 2),
        "Profit Factor": round(gross_profit / abs(gross_loss), 2) if gross_loss else float("inf"),
        "Max Drawdown %": round(M.max_drawdown(net) * 100, 2),
        "Max Run-up %": round(runup, 2),
        "Commission Paid $": round(comm, 2),
        "Total Closed Trades": len(trades),
        "Percent Profitable %": round(len(wins) / len(trades) * 100, 2) if len(trades) else 0,
        "Avg Trade %": round(trades.return_pct.mean(), 2) if len(trades) else 0,
        "Avg Win %": round(wins.return_pct.mean(), 2) if len(wins) else 0,
        "Avg Loss %": round(losses.return_pct.mean(), 2) if len(losses) else 0,
        "Ratio AvgWin/AvgLoss": round(abs(wins.return_pct.mean() / losses.return_pct.mean()), 2)
            if len(wins) and len(losses) and losses.return_pct.mean() != 0 else float("inf"),
        "Largest Win %": round(trades.return_pct.max(), 2) if len(trades) else 0,
        "Largest Loss %": round(trades.return_pct.min(), 2) if len(trades) else 0,
        "Avg Bars in Trades": round(trades.bars.mean(), 1) if len(trades) else 0,
        "Avg Bars in Wins": round(wins.bars.mean(), 1) if len(wins) else 0,
        "Avg Bars in Losses": round(losses.bars.mean(), 1) if len(losses) else 0,
        "Sharpe Ratio": round(M.sharpe(net, BPY), 2),
        "Sortino Ratio": round(M.sortino(net, BPY), 2),
        "CAGR %": round(M.cagr(net, BPY) * 100, 2),
        "Time in Market %": round(res.gross_exposure_time * 100, 1),
    }
    return s, trades, net, bh_ret, equity


def year_table(net, bh):
    d = pd.DataFrame({"strat": net, "bh": bh})
    out = d.groupby(d.index.year).apply(lambda g: pd.Series({
        "strategy_%": round(((1 + g["strat"]).prod() - 1) * 100, 1),
        "buy_hold_%": round(((1 + g["bh"]).prod() - 1) * 100, 1),
    }))
    out["outperformance_%"] = (out["strategy_%"] - out["buy_hold_%"]).round(1)
    return out


def rolling_table(net, bh):
    rows = []
    for label, w in [("30d", 30), ("90d", 90), ("180d", 180), ("365d", 365)]:
        rs = M.rolling_returns(net, w).dropna() * 100
        rb = M.rolling_returns(bh, w).dropna() * 100
        rows.append({
            "window": label,
            "strat_min_%": round(rs.min(), 1), "strat_med_%": round(rs.median(), 1),
            "strat_max_%": round(rs.max(), 1), "strat_%pos": round((rs > 0).mean() * 100, 1),
            "bh_med_%": round(rb.median(), 1), "bh_%pos": round((rb > 0).mean() * 100, 1),
            "strat_beats_bh_%": round((rs.values > rb.reindex(rs.index).values).mean() * 100, 1),
        })
    return pd.DataFrame(rows)


def main():
    df = fetch_klines("BTCUSDT", "1d", "2017-08-01")
    df = df[~df.index.duplicated()].sort_index().loc["2018-06-01":]
    sig = trend_ensemble(df, threshold=THRESHOLD)
    s, trades, net, bh, equity = tv_summary(df, sig)

    print(f"\n{'='*60}")
    print(f" TRADINGVIEW-STYLE STRATEGY TESTER  |  trend_ensemble")
    print(f" Symbol BTCUSDT  |  Timeframe: 1D (DAILY)  |  Long/Flat")
    print(f" {df.index[0].date()} -> {df.index[-1].date()}  |  Initial ${INIT:,.0f}")
    print(f"{'='*60}")
    print("\n--- PERFORMANCE SUMMARY ---")
    for k, v in s.items():
        print(f"  {k:<24}: {v:>14}")

    yt = year_table(net, bh)
    print("\n--- YEAR-BY-YEAR RETURNS (strategy vs buy & hold) ---")
    print(yt.to_string())

    rt = rolling_table(net, bh)
    print("\n--- ROLLING RETURNS ---")
    print(rt.to_string(index=False))

    # Persist
    pd.Series(s).to_csv(REPORTS / "tv_performance_summary.csv")
    yt.to_csv(REPORTS / "tv_yearly_returns.csv")
    rt.to_csv(REPORTS / "tv_rolling_returns.csv", index=False)
    trades.to_csv(REPORTS / "tv_trades.csv", index=False)
    print(f"\n[report] wrote 4 CSVs to reports/ (summary, yearly, rolling, trades[{len(trades)}])")

    _plots(net, bh, yt)


def _plots(net, bh, yt):
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(2, 1, figsize=(13, 10))
        # yearly bars
        x = np.arange(len(yt)); w = 0.4
        ax[0].bar(x - w/2, yt["strategy_%"], w, label="trend_ensemble", color="#2563eb")
        ax[0].bar(x + w/2, yt["buy_hold_%"], w, label="buy & hold", color="#111111")
        ax[0].set_xticks(x); ax[0].set_xticklabels(yt.index)
        ax[0].axhline(0, color="gray", lw=0.8)
        ax[0].set_title("Year-by-year returns (%)"); ax[0].legend(); ax[0].grid(alpha=0.3, axis="y")
        # rolling 1y
        rs = M.rolling_returns(net, 365).dropna() * 100
        rbb = M.rolling_returns(bh, 365).dropna() * 100
        ax[1].plot(rs.index, rs.values, label="trend_ensemble 1y rolling", color="#2563eb")
        ax[1].plot(rbb.index, rbb.values, label="buy & hold 1y rolling", color="#111111", alpha=0.7)
        ax[1].axhline(0, color="gray", lw=0.8)
        ax[1].set_title("Rolling 365-day total return (%)"); ax[1].legend(); ax[1].grid(alpha=0.3)
        fig.tight_layout(); fig.savefig(REPORTS / "tv_returns.png", dpi=120)
        print("[plot] wrote reports/tv_returns.png")
    except Exception as e:
        print(f"[plot] skipped ({e})")


if __name__ == "__main__":
    main()
