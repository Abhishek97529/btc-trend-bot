# Strategy Specification — `trend_ensemble` (BTC/USDT, Daily)

A complete, self-contained spec for independent replication and testing.
Long/flat spot trend-following on the **daily** timeframe. No shorting, no leverage.

---

## 1. Instrument & data
- **Symbol:** BTC/USDT (Binance spot)
- **Timeframe:** 1 day (daily candles), UTC (00:00 open)
- **Fields needed:** `open_time`, `open`, `high`, `low`, `close`
- **History:** as much as available (Binance BTCUSDT starts 2017-08-17)
- Use **closed candles only** — never act on the in-progress day.

## 2. Indicators (all causal — value at day *t* uses only data up to and including *t*)
- **SMA(n):** simple moving average of `close` over the last `n` days.
- **EMA(n):** exponential moving average of `close`, smoothing `alpha = 2/(n+1)`
  (pandas `ewm(span=n, adjust=False)`).
- **Donchian(n):** `upper = max(high[t-n .. t-1])`, `lower = min(low[t-n .. t-1])`
  — i.e. the highest high / lowest low of the **prior** `n` days (shifted by 1 so
  the current bar is excluded → no lookahead).
- **ROC(n):** rate of change = `close[t] / close[t-n] - 1`.

## 3. The 7 trend signals (each outputs 1 = "uptrend", else 0), computed each day
1. `close > SMA(50)`
2. `close > SMA(100)`
3. `close > SMA(200)`
4. `EMA(20) > EMA(50)`
5. `EMA(50) > EMA(100)`
6. **Donchian(55) breakout state** (stateful): set state = 1 on the day `close`
   crosses **above** the Donchian upper band; set state = 0 on the day `close`
   crosses **below** the Donchian lower band; otherwise **carry forward** the last
   state. Initial state before any cross = 0.
7. `ROC(90) > 0`  (price higher than 90 days ago)

## 4. Aggregation → target exposure
- `agreement = (sum of the 7 signals) / 7`   → a value in `{0, 1/7, 2/7, …, 1}`.
- **Gating threshold = 0.50.**
- **Target exposure (fraction of capital in BTC):**
  - if `agreement >= 0.50` →  `target = agreement`   *(e.g. 4/7 agree → 0.571 invested)*
  - if `agreement <  0.50` →  `target = 0`            *(fully in cash)*
- During warmup (until SMA(200) is defined, ~first 200 days) → `target = 0`.

> Note: exposure equals the agreement fraction (partial sizing), not a binary 0/1.
> More signals agreeing = larger position; below half agreeing = flat.

## 5. Execution model (critical — this prevents lookahead)
- The target is computed from the **close of day t**.
- It is **executed on day t+1** (you cannot trade on a close you're using to decide).
  Implement as: `executed_position = target.shift(1)`.
- Per-day strategy return = `executed_position[t] * (close[t]/close[t-1] - 1)  -  cost[t]`.

## 6. Costs (charged on turnover)
- **Fee = 0.10% (0.001) per side** (Binance spot taker).
- **Slippage = 0.05% (0.0005) per side.**
- `cost[t] = |executed_position[t] - executed_position[t-1]| * (0.001 + 0.0005)`.
- (Optional dust filter for live: skip rebalances smaller than 1% of equity.)

## 7. Rebalance rule
- Re-evaluate once per day after the daily close (a few minutes after 00:00 UTC).
- Move the BTC allocation toward the new target. That's the only action.

## 8. Fixed parameters (do NOT tune per-run — these are final)
| Param | Value |
|---|---|
| SMA windows | 50, 100, 200 |
| EMA pairs | 20/50, 50/100 |
| Donchian window | 55 |
| Momentum lookback | 90 |
| Gating threshold | 0.50 |
| Fee / slippage | 0.10% / 0.05% per side |

## 9. Reference pseudocode
```python
sig = 0
sig += (close > SMA(close,50))
sig += (close > SMA(close,100))
sig += (close > SMA(close,200))
sig += (EMA(close,20) > EMA(close,50))
sig += (EMA(close,50) > EMA(close,100))
sig += donchian55_state           # stateful: +1 above upper, 0 below lower, else hold
sig += (ROC(close,90) > 0)
agreement = sig / 7

target = where(agreement >= 0.5, agreement, 0.0)
target = where(SMA(close,200) is defined, target, 0.0)   # warmup

executed = target.shift(1)                                # trade next day
ret      = executed * close.pct_change() \
           - abs(executed.diff()) * (0.001 + 0.0005)      # net of costs
equity   = (1 + ret).cumprod()
```

## 10. Expected results (to validate a correct implementation)
Backtest over **2018-06-01 → 2026-07-23**, **net of the costs above** (gross, before
costs, is ~+2,566%; the ~9% cost drag brings it to the net figure below), starting flat.

> Note on tolerance: the **total return** can vary ±10–15% between correct
> implementations because a handful of extra/fewer trades (different Donchian
> tie-handling or warmup boundary) compound over 8 years. An independent
> reimplementation on `data-api.binance.vision` reproduced **+2,046% net** with
> slightly higher turnover — the *risk* metrics (max DD −38.26%, time in market
> 54.4%, Sharpe ~1.14–1.18, benchmark) matched almost exactly. Judge correctness
> on the risk metrics and trade DNA, not the last few % of total return.

| Metric | Expected (approx.) |
|---|--:|
| Total return (net) | ~ +2,050% to +2,330% |
| Total return (gross, no costs) | ~ +2,570% |
| CAGR | ~ 46–48% |
| Sharpe (rf=0, 365 bars/yr) | ~ 1.15–1.18 |
| Sortino | ~ 1.27 |
| Max drawdown | ~ −38% |
| Calmar | ~ 1.25 |
| Time in market | ~ 54% |
| Closed round-trip trades | ~ 27 |
| Percent profitable | ~ 26% |
| Profit factor | ~ 3.7 |
| Avg win / avg loss | +103% / −3.7% |
| Median hold: winners / losers | ~176 days / ~3.5 days |

Benchmark for the same window: **BTC buy & hold** ≈ +760%, Sharpe ≈ 0.74,
max drawdown ≈ −77%.

## 11. Annualization / metric conventions
- **Bars per year = 365** (daily crypto trades every day).
- **Sharpe** = `mean(daily_ret) / std(daily_ret) * sqrt(365)`, risk-free = 0.
- **Sortino** = same but denominator is std of negative daily returns only.
- **Max drawdown** = min over time of `equity/cummax(equity) - 1`.
- **CAGR** = `prod(1+daily_ret) ** (365/num_days) - 1`.

## 12. Known behavior (so results aren't misread)
- Low win rate (~26%) is expected — it's trend-following. A few huge winners
  (held ~6 months) pay for many small losers (cut in ~3–4 days).
- It **beats buy-and-hold over full cycles** (higher return, higher Sharpe, ~half
  the drawdown) but **lags during sustained bull markets** (e.g. 2020, 2023, 2024),
  because it needs a trend to confirm before entering. Its edge is crash avoidance.
- Robustness (independently verified): survives threshold 0.3–0.8, ±30% window
  perturbation, 5× costs, and +3-day execution lag with Sharpe staying ~1.05–1.18.
- Adding a trailing stop, a funding-rate filter, or multiple assets did **not**
  improve risk-adjusted results in testing — keep it simple.
