# Fixed MA250 four-hour paper deployment

**Status:** two frozen prospective paper tests. Neither strategy is approved
for live capital.

Both accounts use Binance BTCUSDT perpetual four-hour candles in UTC, calculate
SMA(250) on the completed candle, and execute a direction change at the next
four-hour candle open. Trading costs are 0.04% fee plus 0.03% modeled slippage
on changed notional. Funding is signed, and liquidation monitoring uses mark
price.

## Account A: fixed 2x long / flat

- Close above SMA250: target +2.00x.
- Close at or below SMA250: target 0.00x.
- Historical reference, Sep 2019-Jul 2026: total return +4,994.0%, CAGR
  76.97%, Sharpe 1.17, maximum drawdown -63.89%.

## Account B: fixed 2x long / 0.5x short

- Close above SMA250: target +2.00x.
- Close at or below SMA250: target -0.50x.
- Historical reference, Sep 2019-Jul 2026: total return +7,021.8%, CAGR
  85.79%, Sharpe 1.23, maximum drawdown -57.4%.

## Change control

- The accounts have independent state, status, and trade ledgers.
- There is no volatility sizing and no scheduled rebalance.
- Do not tune parameters from paper results.
- Any rule, execution, cost, market-data, or accounting change creates a new
  candidate and requires full revalidation.
- Paper testing does not authorize real orders.
