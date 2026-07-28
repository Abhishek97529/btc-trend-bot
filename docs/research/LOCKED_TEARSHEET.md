# LOCKED SPOT Strategy — Tear Sheet

BTC/USDT `trend_ensemble`, frozen 2026-07-23. All figures net of costs, no lookahead,
straight from [src/config.py](../../src/config.py). Reproduce with:

```bash
python src/locked_report.py     # params + headline + YoY + trade log
python src/locked_rolling.py    # rolling returns
```

Sample: **2018-06-01 → 2026-07-23** (2,975 daily bars).

---

## Parameters (frozen — do not tune per run)

| | |
|---|---|
| Instrument | BTCUSDT **spot**, daily, long/flat — **no leverage, no shorts** |
| Strategy | `trend_ensemble`, agreement gate = **0.50** |
| 7 trend votes | close>SMA50/100/200 · EMA20>50 · EMA50>100 · Donchian(55) breakout · ROC(90)>0 |
| Sizing | fractional = fraction of votes agreeing (gated at 0.50) |
| Warmup | 260 days (needs SMA200) |
| Costs/side | fee 0.10% + slippage 0.05%, charged on turnover |
| Capital | Rs 10,000 · 365 bars/yr |
| Execution | signal on close of bar t, trade on t+1 (no lookahead) |

---

## Headline (full cycle, net of costs)

| Metric | LOCKED | Buy & Hold |
|---|--:|--:|
| Net profit | **+2,326.4%** (Rs 10,000 → Rs 242,637) | +762.2% |
| CAGR | **47.88%** | — |
| Ann. volatility | 40.1% | — |
| Sharpe | **1.18** | — |
| Sortino | 1.27 | — |
| Max drawdown | **−38.26%** | — |
| Calmar | 1.25 | — |
| Time in market | 54.0% | 100% |
| Closed trades | 27 | — |
| Profit factor | 3.67 | — |

---

## Year-over-year

| Year | Strat % | Buy&Hold % | Days in mkt | In-mkt % | Trades | MaxDD % | Sharpe |
|---|--:|--:|--:|--:|--:|--:|--:|
| 2018 | 0.0 | −50.8 | 0 | 0 | 0 | 0.0 | — |
| 2019 | **+119.9** | +94.3 | 208 | 57% | 27 | −28.4 | 1.74 |
| 2020 | +164.3 | **+302.0** | 300 | 82% | 32 | −26.1 | 2.27 |
| 2021 | **+72.7** | +59.8 | 257 | 70% | 41 | −32.3 | 1.18 |
| 2022 | **−11.6** | **−64.2** | 15 | 4% | 5 | −12.1 | −1.74 |
| 2023 | +65.2 | +155.6 | 278 | 76% | 35 | −22.2 | 1.52 |
| 2024 | +67.8 | +121.3 | 277 | 76% | 45 | −32.5 | 1.42 |
| 2025 | **+4.2** | −6.3 | 229 | 63% | 49 | −16.1 | 0.29 |
| 2026 | −5.3 | −26.0 | 43 | 21% | 10 | −7.5 | −1.23 |

The edge in one row: the **2022 bear cost only −11.6% vs buy & hold's −64.2%** (just 4% time in
market). Crash-avoidance is where the Sharpe advantage comes from; in strong bulls the strategy
keeps pace or wins but does not out-run a full-time B&H.

---

## Trade-by-trade holding log (27 in-market spells)

A spell = a contiguous stretch with executed position > 0. `days_held` = calendar days from
first to last in-market bar; exposure can vary within a spell (fractional sizing).

| Entry | Exit | Days held | Avg expo | Price in → out | Return % |
|---|---|--:|--:|--:|--:|
| 2019-02-24 | 2019-02-24 | 1 | 0.71 | 3,744 → 3,744 | −6.6 |
| 2019-02-26 | 2019-09-20 | **207** | 0.88 | 3,809 → 10,169 | **+135.9** |
| 2020-01-15 | 2020-03-08 | 54 | 0.86 | 8,821 → 8,033 | −11.6 |
| 2020-04-30 | 2021-05-15 | **381** | 0.94 | 8,620 → 46,763 | **+389.9** |
| 2021-08-01 | 2021-08-01 | 1 | 0.57 | 39,845 → 39,845 | −2.3 |
| 2021-08-05 | 2021-09-28 | 55 | 0.86 | 40,863 → 41,027 | −6.6 |
| 2021-09-30 | 2021-12-04 | 66 | 0.94 | 43,824 → 49,153 | +16.1 |
| 2022-03-28 | 2022-04-11 | 15 | 0.65 | 47,122 → 39,530 | −11.5 |
| 2023-01-14 | 2023-06-05 | 143 | 0.93 | 20,955 → 25,728 | +26.4 |
| 2023-06-07 | 2023-06-07 | 1 | 0.57 | 26,339 → 26,339 | −2.0 |
| 2023-06-21 | 2023-08-17 | 58 | 0.94 | 29,994 → 26,623 | −6.5 |
| 2023-10-17 | 2024-05-01 | **198** | 0.96 | 28,396 → 58,365 | **+97.9** |
| 2024-05-04 | 2024-05-10 | 7 | 0.57 | 63,892 → 60,800 | −2.0 |
| 2024-05-14 | 2024-05-14 | 1 | 0.57 | 61,578 → 61,578 | −1.3 |
| 2024-05-16 | 2024-06-18 | 34 | 0.79 | 65,235 → 65,175 | −1.0 |
| 2024-06-21 | 2024-06-22 | 2 | 0.57 | 64,144 → 64,262 | −0.6 |
| 2024-07-16 | 2024-07-17 | 2 | 0.57 | 65,044 → 64,088 | −0.6 |
| 2024-07-20 | 2024-08-02 | 14 | 0.81 | 67,140 → 61,498 | −6.7 |
| 2024-09-23 | 2024-10-01 | 9 | 0.65 | 63,340 → 60,806 | −3.3 |
| 2024-10-05 | 2024-10-09 | 5 | 0.57 | 62,058 → 60,636 | −1.4 |
| 2024-10-12 | 2025-02-19 | 131 | 0.95 | 63,206 → 96,644 | **+46.8** |
| 2025-02-21 | 2025-02-21 | 1 | 0.57 | 96,182 → 96,182 | −1.3 |
| 2025-02-24 | 2025-02-24 | 1 | 0.57 | 91,553 → 91,553 | −2.9 |
| 2025-04-25 | 2025-10-17 | 176 | 0.90 | 94,639 → 106,432 | +8.4 |
| 2025-10-27 | 2025-10-27 | 1 | 0.57 | 114,108 → 114,108 | −0.3 |
| 2026-04-18 | 2026-04-19 | 2 | 0.57 | 75,692 → 73,802 | −2.5 |
| 2026-04-21 | 2026-05-31 | 41 | 0.65 | 76,336 → 73,674 | −2.7 |

**Holding stats:** avg hold **60 days**, median **14 days**, longest **381 days**.
**7 of 27 spells (26%) were winners** — but winners are huge (+390%, +136%, +98%, +47%) and
the 20 losers are tiny (worst −11.6%). Classic trend profile: few big wins, many small
paper-cuts, profit factor 3.67. The long spells (381d/207d/198d) carry all the performance;
the 1-day spells are cheap false starts as the ensemble briefly ticks above the 0.50 gate.

---

## Rolling total returns (all overlapping windows)

| Series | Window | # windows | Min % | 5th | Median | Mean | 95th | Max % | **% positive** |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| **LOCKED** | 30d | 2,946 | −26.2 | −11.3 | 0.0 | 4.2 | 35.9 | 119.6 | 35% |
| Buy&Hold | 30d | 2,946 | −53.0 | −24.9 | 1.6 | 4.1 | 39.7 | 119.6 | 54% |
| **LOCKED** | 90d | 2,886 | −25.9 | −14.7 | 0.0 | 15.2 | 101.1 | 259.3 | 46% |
| Buy&Hold | 90d | 2,886 | −58.5 | −40.3 | 4.7 | 15.3 | 109.3 | 259.4 | 55% |
| **LOCKED** | 180d | 2,796 | −32.8 | −17.3 | 9.2 | 35.7 | 176.1 | 461.9 | 61% |
| Buy&Hold | 180d | 2,796 | −60.7 | −48.4 | 16.4 | 35.9 | 212.6 | 473.4 | 60% |
| **LOCKED** | 365d | 2,611 | **−34.2** | **−16.5** | 46.7 | 82.2 | 378.5 | 558.4 | **78%** |
| Buy&Hold | 365d | 2,611 | **−76.2** | **−55.9** | 52.0 | 88.6 | 356.4 | 1092.1 | 70% |

`% positive` = share of overlapping N-day windows that finished green (a hold-for-N-days
historical win rate).

### Best / worst windows (LOCKED)

| Window | Best | When | Worst | When (ending) |
|---|--:|---|--:|---|
| 30d | +119.6% | Dec 2020 → Jan 2021 | −26.2% | 2019-07-26 |
| 90d | +259.3% | Oct 2020 → Jan 2021 | −25.9% | 2019-09-24 |
| 180d | +461.9% | Sep 2020 → Mar 2021 | −32.8% | 2022-05-07 |
| 365d | +558.4% | Apr 2020 → Apr 2021 | **−34.2%** | 2020-06-25 |

**Reading it:** the edge is downside protection and it grows with horizon — worst 1-year
**−34% vs B&H −76%**, worst 6-month −33% vs −61%. Short horizons look weak on win-rate
(only 35% of 30-day windows green) purely because the strategy sits in cash ~46% of the time
(median 30d/90d return = exactly 0.0%). Hold it a year and it finishes green **78%** of the
time with roughly half the downside. It is a position-trend system — the 1-year distribution
is where the Sharpe 1.18 lives.
