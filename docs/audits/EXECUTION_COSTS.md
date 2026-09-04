# Execution cost assumptions

Measured 2026-09-04. Regenerate with `python src/measure_execution_costs.py`.

## Measured slippage

A market-order sweep of the live order books at the notionals these accounts
actually trade ($10k-$50k):

| Venue | Spread | $10k | $20k | $50k |
|---|---:|---:|---:|---:|
| Binance spot BTCUSDT | 0.00 bps | 0.00 bps | 0.00 bps | 0.00 bps |
| Bybit perp BTCUSDT | 0.01 bps | 0.01 bps | 0.01 bps | 0.04 bps |

BTCUSDT is far too deep for this size to move the book. Book-impact slippage is
effectively zero, so the configured 3-5 bps allowance is a conservative buffer
covering latency between signal and fill rather than depth. The real cost driver
is the fee, not slippage.

## Fees

These runners cross the spread, so the **taker** rate applies. The perpetual
configs previously assumed 4 bps, which is the Bybit VIP 1 taker rate, not the
VIP 0 rate the accounts actually pay.

| Config | Fee | Slippage | Basis |
|---|---:|---:|---|
| `ma250_4h_long_flat` | 5.5 bps | 3 bps | Bybit VIP 0 perpetual taker |
| `ma250_4h_long_short` | 5.5 bps | 3 bps | Bybit VIP 0 perpetual taker |
| `spot_4h_dual_trend` | 10 bps | 5 bps | Binance VIP 0 spot taker |
| `spot_4h_dual_trend_shadow` | 10 bps | 5 bps | Binance VIP 0 spot taker |
| `daily_spot_ensemble` | 10 bps | 5 bps | Binance VIP 0 spot taker |

Bybit VIP 0 perpetual maker is 2 bps. Posting passive orders instead of crossing
would cut perpetual cost by roughly two thirds, at the cost of fill uncertainty
on a 4h bar. That trade-off has not been tested and the runners remain taker.

## Open item: the spot venue is not settled

The spot rows above are Binance rates, which is where the signal data comes
from. If these strategies are executed on CoinDCX instead, the cost is much
higher and the conclusions change materially. Per the deployment audit, the
CoinDCX regular INR tier reaches 0.50% plus GST, roughly 0.64% per side with
slippage; the dual-trend rule needs roughly **0.383% per side** to beat
buy-and-hold. At INR regular-tier cost the strategy fails its own objective,
while the lower crypto-to-crypto tier (~0.17% before GST) still clears it.

Before any live decision, confirm the actual account tier, pair, GST, TDS
treatment, and measured fill quality, then set `FEE`/`SLIPPAGE` from that
measurement rather than from a published headline rate.
