# Frozen 4-hour BTC paper candidate

**Status:** frozen for prospective paper testing only. Not approved for live capital.

## Canonical rule

1. Use Binance BTCUSDT perpetual four-hour candles in UTC.
2. On every completed candle, calculate SMA(250).
3. If close is above SMA(250), target a long position.
4. If close is below SMA(250), target a short position.
5. Calculate annualized realized volatility from the previous 60 four-hour returns.
6. Raw exposure is `0.40 / realized_annual_volatility`.
7. Clamp raw exposure to a minimum of 0.25×.
8. Cap long exposure at 2.00× and short exposure at 0.50×.
9. Execute direction changes at the next four-hour candle open.
10. Rebalance exposure every 42 bars, anchored to the global Binance candle grid.
11. Charge 0.04% fee plus 0.03% slippage on actual changed notional.
12. Apply signed Binance funding: longs pay positive funding and shorts receive it.
13. Liquidation and margin monitoring use Binance mark price.

Frozen parameters live in `src/dynamic_4h_config.py`.

## Corrected historical reference

| Metric | Real perp 2019–2026 | Extended proxy 2017–2026 |
|---|---:|---:|
| Total return | +1,610.2% | +3,702.5% |
| CAGR | 51.03% | 50.22% |
| Sharpe | 1.26 | 1.24 |
| Maximum drawdown | −32.9% | −51.3% |
| Calmar | 1.55 | 0.98 |

## Predetermined periods

| Period | Total return | CAGR | Sharpe | Maximum drawdown |
|---|---:|---:|---:|---:|
| Development 2019–2022 | +284.6% | 50.11% | 1.24 | −32.9% |
| Validation 2023–2024 | +330.6% | 107.31% | 1.97 | −29.0% |
| 2025+ diagnostic | +3.1% | 2.00% | 0.23 | −29.0% |

The 2025+ period has already been inspected and is not a fresh holdout.

## Stress reference

| Test | CAGR | Sharpe | Maximum drawdown |
|---|---:|---:|---:|
| Base | 51.03% | 1.26 | −32.9% |
| One-bar additional delay | 50.44% | 1.26 | −37.6% |
| Cost 0.15% per side | 42.42% | 1.11 | −36.0% |
| Funding ×1.5 | 46.33% | 1.18 | −34.7% |
| Funding ×2 | 41.76% | 1.10 | −36.4% |

Bootstrap: 0.6% terminal-loss incidence, −46.6% median maximum drawdown,
36.1% incidence below −50%, and 2.2% incidence below −70%.

## Change control

- Do not tune parameters using paper results.
- Do not silently substitute spot prices, another candle anchor, or last price for mark price.
- Any parameter or execution change creates a new candidate and requires the full validation battery.
- Run at least six months, preferably twelve months, before considering live capital.
- Paper testing does not authorize live orders.
