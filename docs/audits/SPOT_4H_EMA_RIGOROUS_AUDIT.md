# Rigorous audit — BTCUSDT spot EMA(24,168), four-hour

Audit date: 2026-07-28  
Verdict: **historically strong, robust paper candidate; not live-validated**

## Exact implementation

- BTCUSDT spot; four-hour UTC candles.
- EMA(24) above EMA(168): 100% long.
- Otherwise: cash.
- Signal uses the completed close and executes at the next four-hour open.
- Long/flat, no leverage or shorting.
- Cost: 0.10% fee plus 0.05% slippage per side.
- Available 2017-2018 history warms both EMAs before scoring begins in 2019.

## Full-sample result

2019-01-01 through 2026-07-27:

| Metric | Strategy | Buy and hold |
|---|---:|---:|
| Total return | +2,919.1% | +1,667.0% |
| CAGR | 56.9% | 46.2% |
| Sharpe | 1.28 | 0.93 |
| Maximum drawdown | -46.3% | -77.1% |
| Exposure | 53.6% | 100% |
| Entries / exits | 63 / 62 | 1 / 0 |

Rs 1 lakh became approximately Rs 30.2 lakh, versus Rs 17.7 lakh for
buy-and-hold.

## Year by year

`Orders` counts entries plus exits. Maximum drawdown is reset at the start of
each calendar year.

| Year | Strategy | B&H | Edge | Max DD | B&H DD | Entries | Exits | Orders | Exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2019 | +100.7% | +94.4% | +6.3pp | -41.2% | -52.6% | 5 | 5 | 10 | 53.3% |
| 2020 | +268.7% | +302.0% | -33.3pp | -21.6% | -56.0% | 12 | 11 | 23 | 70.1% |
| 2021 | +103.9% | +59.8% | +44.2pp | -32.2% | -54.1% | 6 | 7 | 13 | 58.6% |
| 2022 | -38.8% | -64.2% | +25.5pp | -43.5% | -67.2% | 9 | 9 | 18 | 25.6% |
| 2023 | +81.3% | +155.6% | -74.3pp | -20.6% | -21.2% | 9 | 8 | 17 | 64.4% |
| 2024 | +95.3% | +121.3% | -26.0pp | -25.3% | -30.0% | 7 | 8 | 15 | 65.4% |
| 2025 | -6.6% | -6.3% | -0.3pp | -20.5% | -34.4% | 9 | 9 | 18 | 45.3% |
| 2026* | -1.2% | -25.4% | +24.2pp | -13.3% | -40.0% | 6 | 5 | 11 | 39.9% |

The strategy beat buy-and-hold in four of eight calendar years. Its full-sample
advantage comes from compounding protection during deep declines, not consistent
annual outperformance.

## Chronological validation

| Period | Strategy | B&H | Strategy Sharpe | Strategy DD |
|---|---:|---:|---:|---:|
| 2019-2022 | +824.1% | +346.9% | 1.37 | -46.3% |
| 2023-2024 | +254.1% | +465.7% | 1.86 | -25.3% |
| 2025+ | -7.7% | -30.1% | -0.10 | -26.0% |
| Preselected OOS boundary, 2023-07-15+ | +151.1% | +115.8% | 1.13 | -28.7% |

The selected OOS boundary passes, but all history has now been inspected.
Only data after the freeze date is genuinely prospective.

## Stress tests

| Scenario | Return | CAGR | Sharpe | Max DD |
|---|---:|---:|---:|---:|
| Base | +2,919% | 56.9% | 1.28 | -46.3% |
| Cost 0.30%/side | +2,402% | 53.0% | 1.22 | -47.8% |
| Cost 0.45%/side | +1,972% | 49.3% | 1.16 | -49.4% |
| Cost 0.80%/side | +1,234% | 40.8% | 1.02 | -52.8% |
| Additional one-bar delay | +2,770% | 55.8% | 1.26 | -51.9% |
| Additional two-bar delay | +2,266% | 51.9% | 1.20 | -56.4% |
| Additional five-bar delay | +1,828% | 47.9% | 1.13 | -62.6% |

The edge survives substantial friction and delay. At the highest cost stress it
no longer beats the normal-cost buy-and-hold benchmark.

## Parameter robustness

A 30-configuration grid used fast EMAs 12-48 and slow EMAs 120-240. The selected
24/168 pair ranked fourth by Sharpe rather than first. Several neighboring
configurations produced similar results, supporting a broad slow-trend effect.
However, returns varied greatly and the best grid result exceeded +3,700%, so
selection bias remains material.

## Bootstrap uncertainty

Two thousand 30-day-block bootstrap paths:

| Quantile | CAGR | Sharpe | Max DD |
|---|---:|---:|---:|
| 5th percentile | 18.7% | 0.62 | -67.4% |
| Median | 57.4% | 1.27 | -47.4% |
| 95th percentile | 114.5% | 1.92 | -33.9% |

This measures uncertainty conditional on historical regimes. It cannot model a
structural disappearance of BTC's trend behavior.

## Trade concentration

- 62 closed trades.
- Win rate: 32.3%.
- Median trade: -3.1%.
- Average winner: +37.9%.
- Average loser: -4.8%.
- The five largest winners account for almost all terminal wealth; other trades
  compound to approximately +12%.
- The ten largest winners account for more than terminal wealth; all remaining
  trades compound to approximately -72%.

This is classic trend-following behavior: many small false starts and a few very
large trends. Missing one major entry can materially change the outcome.

## Final decision

The candidate passes mechanical, cost, delay, neighborhood, and one chronological
OOS test. It fails the stronger claim of consistent yearly outperformance and
has no remaining untouched historical data.

Keep it frozen as a prospective paper strategy for at least 12 months. Capital
planning should assume a 55%-70% adverse drawdown, not the historical 46% alone.
Do not replace the daily locked strategy or deploy live funds based solely on
this backtest.

## Rejected extension: shorting below the EMA cross

Adding a negative position when EMA(24) is at or below EMA(168) changes the
instrument from spot to margin or perpetual futures. It was tested separately
on real Binance BTCUSDT perpetual candles from 2019-09-08 through 2026-07-27,
with historical funding, exact next-open execution, and 8.9 bps cost per changed
notional.

| Bear-regime exposure | Return | CAGR | Sharpe | Max DD |
|---|---:|---:|---:|---:|
| 0× (long/flat baseline) | +913.8% | 40.0% | 1.04 | -46.2% |
| -0.25× short | +872.5% | 39.2% | 1.00 | -40.3% |
| -0.50× short | +759.6% | 36.7% | 0.91 | -44.2% |
| -1.00× short | +425.6% | 27.3% | 0.70 | -63.2% |

The 0.25× short bought about six percentage points of drawdown reduction at the
cost of lower return and Sharpe. Larger shorts were clearly worse. Requiring the
bearish EMA separation to reach 2%, 5%, or 10% before shorting did not improve
the conclusion and increased regime-switch orders in some variants.

Decision: **do not add shorts**. Retain cash as the bear-regime position.
