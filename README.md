# BTC/USDT Trend-Ensemble Trading Bot

A research-first crypto trading project. We designed candidate strategies, backtested
and forward-tested them honestly against buy-and-hold, validated the survivor with a
full robustness battery, and wrapped it in a **paper-trading bot**.

> **Honest headline:** over a full market cycle (incl. the 2022 bear), the
> `trend_ensemble` strategy beats buy-and-hold on return, Sharpe, *and* drawdown.
> Its edge is **crash avoidance** — it will *lag* during sustained bull runs. It is
> not a get-rich-quick machine. All results are net of 0.10% fee + 5 bps slippage
> per side and are out-of-sample.

## Results (validation battery, fixed params, no tuning, 2018→2026)

|                | Total Return | CAGR  | Sharpe | Max Drawdown |
|----------------|-------------:|------:|-------:|-------------:|
| trend_ensemble |      +2,326% | 47.9% |  1.18  |        −38%  |
| Buy & Hold     |        +761% | 30.2% |  0.74  |        −77%  |

Survived: parameter sweeps, ±30% window perturbation, 5× costs, +3-day execution
lag, and a 2,000-path block-bootstrap (89% chance of beating buy-and-hold's Sharpe).

## The strategy: `trend_ensemble`
Votes across 7 fixed-length trend signals (50/100/200-day SMAs, 20/50 & 50/100 EMA
crossovers, 55-day Donchian breakout, 90-day momentum) and sizes exposure by the
fraction that agree. Long/flat, spot-deployable, one coarse knob (threshold=0.5).

## Layout
```
src/
  data_fetch.py     Binance public klines downloader (+ corporate-proxy SSL fix)
  indicators.py     causal technical indicators
  strategies.py     round-1 hourly strategies
  strategies_v2.py  round-2 daily strategies (the ensemble lives here)
  backtest.py       vectorized, no-lookahead engine w/ fees, slippage, shorting
  metrics.py        Sharpe, Sortino, maxDD, Calmar, rolling returns
  research.py       round-1 hourly study
  research_v2.py    round-2 daily study (train/test + walk-forward)
  validate.py       robustness battery
bot/
  paper_bot.py      paper-trading bot (no keys, no real money)
reports/            generated tables + equity-curve charts
data/               cached parquet price data
```

## Setup
```bash
pip install -r requirements.txt
```
Behind a corporate TLS proxy? `truststore` (already imported in `data_fetch.py`)
routes Python through the Windows certificate store — no extra config needed.

## Usage
```bash
python src/research_v2.py     # reproduce the daily study + charts
python src/validate.py        # reproduce the robustness battery
python bot/paper_bot.py reset # start a fresh $10k paper portfolio
python bot/paper_bot.py run   # compute today's signal, rebalance (once/day)
python bot/paper_bot.py status# portfolio + latest signal
```
Run `bot/paper_bot.py run` once daily, a few minutes after 00:00 UTC (Task Scheduler
or `loop`). It acts only on the last *closed* daily bar.

## ⚠️ Risk notes
- Past performance ≠ future results. Crypto is highly volatile; you can lose money.
- The edge is drawdown-avoidance; expect to underperform BTC in strong bull markets.
- Live trading is **not** wired yet — this is paper only. Paper-test for weeks before
  ever risking real capital, and start tiny.
