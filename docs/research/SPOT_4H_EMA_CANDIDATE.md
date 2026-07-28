# BTCUSDT spot 4-hour EMA candidate

Status: **research candidate for prospective paper testing**  
Frozen: 2026-07-28  
This does not replace the locked daily spot strategy.

## Rule

- Binance BTCUSDT spot, four-hour UTC candles.
- Calculate EMA(24) and EMA(168) from completed closes.
- If EMA(24) is above EMA(168), target 100% BTC.
- Otherwise target 100% cash.
- Execute a changed signal at the next four-hour candle open.
- Long/flat only: no shorting, leverage, futures, or funding.
- Charge 0.10% fee plus 0.05% slippage per side.
- Skip operational dust below 1% of equity.

EMA(24) is approximately a four-day trend estimate; EMA(168) is approximately
a 28-day trend estimate.

## Historical result

Data: 2019-01-01 through 2026-07-23.

| Period | Strategy | Buy & hold | Strategy Sharpe | Strategy max DD |
|---|---:|---:|---:|---:|
| Full | +2,919.1% | +1,667.0% | 1.28 | -46.3% |
| 2019-2022 | +824.1% | +346.9% | 1.37 | -46.3% |
| 2023-2024 | +254.1% | +465.7% | 1.86 | -25.3% |
| 2025 onward | -7.7% | -30.1% | -0.10 | -25.9% |

Rs 1 lakh historically became about Rs 30.2 lakh, versus Rs 17.7 lakh for
buy-and-hold. Ending wealth was approximately 1.71 times buy-and-hold.

## Parameter-neighborhood check

Nearby slow trend configurations also beat buy-and-hold over the full sample,
although results vary materially:

| EMA pair | Full return | Max DD |
|---|---:|---:|
| 18 / 144 | +1,885% | -52.6% |
| 24 / 144 | +2,034% | -49.2% |
| 24 / 168 | +2,919% | -46.3% |
| 30 / 168 | +2,072% | -54.0% |
| 36 / 168 | +1,961% | -55.1% |
| 24 / 192 | +2,081% | -55.4% |
| 36 / 192 | +2,565% | -61.8% |

The surrounding region is profitable, but EMA(24,168) is visibly the local
historical winner. Its excess return must therefore be discounted for selection
bias.

## Cost stress

| Fee + slippage per side | Full return | CAGR | Sharpe | Max DD |
|---|---:|---:|---:|---:|
| 0.10% + 0.05% | +2,919% | 56.9% | 1.28 | -46.3% |
| 0.20% + 0.10% | +2,402% | 53.0% | 1.22 | -47.8% |
| 0.30% + 0.15% | +1,972% | 49.3% | 1.16 | -49.4% |
| 0.50% + 0.30% | +1,234% | 40.8% | 1.02 | -52.8% |

The candidate is not dependent on unrealistically cheap execution, although the
highest stress case no longer beats the baseline buy-and-hold return.

## Honest interpretation

This candidate beats buy-and-hold by a considerable margin over the complete
backtest and reduces maximum drawdown. It does not beat buy-and-hold in every
regime: it captured much less of the 2023-2024 bull market and still lost money
from 2025 onward.

The rule was chosen after historical data had been inspected. There is no
remaining pristine historical holdout. Only results after 2026-07-28 can provide
genuinely prospective evidence.

## Promotion gates

- Paper trade unchanged for at least 12 months.
- Reconcile every signal, next-open fill, fee, and slippage estimate.
- Compare prospectively with buy-and-hold, EMA(36,168), and the locked daily
  strategy.
- Require no unexplained implementation divergence.
- Size capital assuming at least a 55% drawdown; historical neighboring
  configurations reached about 62%.
- Do not promote merely because it beats buy-and-hold over a short bull window.
- Do not tune EMA lengths using paper results.

Current decision: **paper candidate only; no live capital authorization**.
