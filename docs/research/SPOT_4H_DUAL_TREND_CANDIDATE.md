# BTCUSDT spot four-hour dual-trend candidate

Status: **frozen research candidate for prospective paper testing**  
Freeze date: 2026-07-28  
This does not replace the locked daily strategy.

## Rule

Use completed BTCUSDT spot four-hour UTC candles. Hold 100% BTC only when all
three conditions are true:

1. EMA(24) > EMA(168);
2. close / close 120 bars ago - 1 > 0;
3. close > SMA(300).

Otherwise hold cash. Execute a changed signal at the next four-hour open.
There is no leverage, shorting, stop loss, take profit, or discretionary override.

The horizons correspond approximately to:

- EMA(24): four days;
- EMA(168): 28 days;
- momentum(120): 20 days;
- SMA(300): 50 days.

Base modeled costs are 0.10% fee plus 0.05% slippage per changed notional.

## Why combine the filters?

The EMA crossover detects a medium-term trend. Positive 20-day momentum prevents
entering a weakening trend, while price above its 50-day average confirms the
larger regime. The conjunction is intentionally selective: historical exposure
was about 45%.

## Historical comparison

Data scored from 2019-01-01 through 2026-07-27, with 2017-2018 history used
to warm all indicators; exact next-open execution.

| Metric | Dual trend | Buy and hold |
|---|---:|---:|
| Total return | +3,897.6% | +1,667.0% |
| CAGR | 62.8% | 46.2% |
| Sharpe | 1.43 | 0.93 |
| Maximum drawdown | -46.5% | -77.1% |
| Exposure | 45.3% | 100% |
| Entries / exits | 176 / 175 | 1 / 0 |

Rs 1 lakh historically became about Rs 40.0 lakh, versus Rs 17.7 lakh for
buy-and-hold. Ending wealth was approximately 2.26 times buy-and-hold.

## Chronological results

| Period | Strategy | Buy and hold | Strategy Sharpe | Strategy DD |
|---|---:|---:|---:|---:|
| 2019-2022 | +1,062.3% | +346.9% | 1.55 | -46.4% |
| 2023-2024 | +224.4% | +465.7% | 1.83 | -29.9% |
| 2025 onward | +6.0% | -30.1% | 0.28 | -24.7% |
| Preselected boundary, 2023-07-15 onward | +153.0% | +115.8% | 1.20 | -29.9% |

The strategy did not beat buy-and-hold during the complete 2023-2024 bull block.
Its advantage is full-cycle compounding and downside avoidance, not continuous
outperformance.

## Year by year

| Year | Strategy | Buy and hold | Strategy DD | Entries | Exits |
|---|---:|---:|---:|---:|---:|
| 2019 | +141.0% | +94.4% | -29.3% | 18 | 18 |
| 2020 | +272.9% | +302.0% | -25.3% | 24 | 23 |
| 2021 | +105.2% | +59.8% | -25.5% | 30 | 31 |
| 2022 | -37.0% | -64.2% | -38.4% | 23 | 23 |
| 2023 | +102.5% | +155.6% | -19.5% | 17 | 16 |
| 2024 | +60.2% | +121.3% | -29.9% | 30 | 31 |
| 2025 | +12.4% | -6.3% | -15.3% | 22 | 22 |
| 2026* | -5.7% | -25.4% | -17.1% | 12 | 11 |

It beat buy-and-hold in five of eight calendar years. 2026 is partial through
July 27.

## Robustness

A 108-configuration neighborhood varied:

- EMA fast: 18, 24, 30;
- EMA slow: 144, 168, 192;
- momentum: 90, 120, 150, 180 bars;
- trend SMA: 240, 300, 360 bars.

106 of 108 configurations beat normal-cost buy-and-hold over the complete
sample. The distribution was:

| Statistic | Return | Sharpe | Max DD |
|---|---:|---:|---:|
| Minimum | +1,622.2% | 1.17 | -51.5% |
| Median | +2,343.0% | 1.27 | -43.8% |
| 95th percentile | +3,865.4% | 1.43 | -39.0% |
| Maximum | +4,606.5% | 1.48 | -34.8% |

The frozen configuration lies near the high end of this range, so its exact
+3,898% result must be discounted for favorable parameter interaction. The
broader trend effect is more credible than the precise headline.

## Cost and delay stress

| Scenario | Return | Sharpe | Max DD |
|---|---:|---:|---:|
| Base, 0.15%/side | +3,898% | 1.43 | -46.5% |
| 0.30%/side | +2,259% | 1.25 | -51.4% |
| 0.45%/side | +1,291% | 1.07 | -55.9% |
| 0.80%/side | +304% | 0.66 | -64.8% |
| One additional bar delay | +3,306% | 1.38 | -48.4% |
| Two additional bars | +2,778% | 1.32 | -47.3% |
| Five additional bars | +2,183% | 1.24 | -55.7% |

This is cost-sensitive because it generated 351 entry/exit orders. At about
0.45% per side it no longer beats normal-cost buy-and-hold. Venue-specific fees
must be checked before paper comparison.

## Trade distribution

- 175 closed trades;
- win rate 31.4%;
- median trade -0.9%;
- average winner +13.0%;
- average loser -2.0%;
- the ten largest winners account for approximately all terminal profit.

This remains a trend-following strategy: many small false starts are funded by a
small number of extended trends.

## Honest decision

This is the strongest simple spot-only candidate found under normal low-cost
execution. It beats buy-and-hold by a considerable full-cycle margin and passes
the preselected July-2023 boundary, but it does not beat buy-and-hold in every
regime and was designed after the historical data was available.

Freeze it unchanged and paper test for at least 12 months. Do not tune using
paper results. Plan capital around a possible 55%-70% drawdown. Only data after
2026-07-28 is genuinely prospective.
