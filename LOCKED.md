# 🔒 LOCKED STRATEGY — BTC Spot Trend Ensemble

**Locked:** 2026-07-23
**Status:** FROZEN. Live-tradeable via paper bot. Do not tune parameters without full re-validation.

This is the official, frozen definition of the BTC strategy we will trade. All
parameters live in [src/config.py](src/config.py) — the single source of truth
that the backtest, paper bot, and Pine port all reference.

---

## What we locked

| Item | Value |
|---|---|
| **Instrument** | BINANCE **spot** BTCUSDT (not `.P` perpetual) |
| **Market type** | **Long / flat only — NO leverage, NO shorting** |
| **Timeframe** | Daily (1D), UTC close |
| **Strategy** | `trend_ensemble` — 7 fixed trend votes, exposure = agreement fraction, gated at 0.50 |
| **Only knob** | Agreement threshold = 0.50 (robust 0.30–0.80) |
| **Costs** | 0.10% fee + 0.05% slippage per side |
| **Rebalance** | Once per day after the closed daily bar; skip moves < 1% of equity |

Full mechanics: [STRATEGY_SPEC.md](STRATEGY_SPEC.md). Pine port: [trend_ensemble.pine](trend_ensemble.pine).

---

## Locked backtest results (2018-06-01 → 2026-07-23, net of costs)

| Metric | Strategy | Buy & Hold |
|---|--:|--:|
| Net profit | **+2,326%** | +762% |
| CAGR | **47.9%** | 30.3% |
| Sharpe | **1.18** | 0.74 |
| Sortino | **1.27** | 1.00 |
| Max drawdown | **−38.3%** | −76.6% |
| Profit factor | 3.67 | — |
| Closed trades | 27 | 1 |
| Percent profitable | 25.9% | — |
| Time in market | 54% | 100% |

**Edge:** beats buy-and-hold on total return, risk-adjusted return (Sharpe), *and*
drawdown over the full cycle. Its advantage is **crash avoidance** — it lags in
sustained bull runs (2020, 2023, 2024) and wins by sidestepping the deep bears.

---

## Decisions locked in (and why)

- **Spot, 1x.** Leverage was tested and rejected: 2x/3x leave Sharpe **unchanged**
  (1.03) while blowing max drawdown to −71% / −89%; 5x is **liquidated** (2021-01-11).
  Leverage adds risk and funding cost, not risk-adjusted return. See
  [src/test_leverage_full.py](src/test_leverage_full.py).
- **BTC only.** Multi-asset (6 coins) did not improve Sharpe and worsened drawdown.
- **No trailing stop.** It cut the winners the strategy depends on.
- **No funding-rate filter.** No robust improvement in testing.
- **Threshold 0.50.** Middle of the robust plateau (0.3–0.8).

---

## Robustness (independently verified — all passed)

Survives threshold 0.3–0.8, ±30% window perturbation, 5× costs, +3-day execution
lag, and block-bootstrap Monte Carlo — Sharpe stays ~1.05–1.18 throughout.

---

## How to run the locked strategy (paper, no keys, no real money)

```bash
python bot/paper_bot.py status    # show portfolio + today's signal
python bot/paper_bot.py run       # compute signal, rebalance once (run daily)
python bot/paper_bot.py loop      # run once/day forever
python bot/paper_bot.py reset     # wipe and start fresh
```

---

## Next step (not yet done)

Live-trading wiring (Binance API keys + real order execution). Deferred until
you have keys and have watched the paper bot track for a while.
