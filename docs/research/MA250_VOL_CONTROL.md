# MA250 volatility-controlled candidate

**Status:** frozen for prospective paper testing; not approved for live capital.

## Rule

- Binance BTCUSDT perpetual, 4-hour candles, UTC.
- Long when the completed close is above SMA(250); flat below it.
- Execute the signal at the next candle open.
- Estimate annualized volatility from the previous 60 four-hour returns.
- Target leverage = `40% / realized annualized volatility`.
- Clamp leverage to 0.25×–2.00×.
- Rebalance leverage every 42 bars (seven days), anchored to the Binance candle grid.
- Exit immediately at the next open after a below-MA signal.
- Charge 0.04% fee and 0.03% slippage on actual changed notional.
- Deduct actual Binance funding from wallet equity.

Frozen parameters are in `src/ma250_vol_config.py`. Research and reproduction are
in `src/improve_ma250_volatility.py`.

## Corrected historical comparison

| Metric | Vol-controlled MA250 | Fixed 2× MA250 |
|---|---:|---:|
| Total return | +1,125.6% | +4,981.0% |
| CAGR | 43.89% | 76.90% |
| Sharpe | 1.26 | 1.17 |
| Maximum drawdown | −36.6% | −63.8% |
| Calmar | 1.20 | 1.20 |
| Bootstrap median maxDD | −42.4% | −67.4% |
| Bootstrap P(maxDD < −70%) | 1.2% | 41.7% |

The improvement is risk control, not maximum historical return.

## Predetermined-period results

| Period | CAGR | Sharpe | Maximum drawdown |
|---|---:|---:|---:|
| Development, 2019–2022 | 32.10% | 1.03 | −36.0% |
| Validation, 2023–2024 | 125.32% | 2.28 | −26.3% |
| 2025+ diagnostic | −2.77% | 0.04 | −34.4% |

The 2025+ data has already been inspected and is diagnostic, not a fresh holdout.
Only future paper results can provide new untouched evidence.

## Paper-stage gates

- Run concurrently with fixed 1×, 1.5×, and 2× MA250 shadow portfolios.
- Do not change parameters for at least six months.
- Reconcile every signal, target leverage, fill, fee, and funding payment.
- Record missed bars, stale data, order delay, and exchange/API failures.
- Do not promote to live capital solely because paper equity is positive.
