# Four-hour spot dual-trend shadow challenger

Experimental paper-only challenger frozen on 2026-07-28. It is an independent
account and does not replace the deployed 24/168/120/300 dual-trend strategy.

Hold BTC only when all three conditions are true on the completed four-hour
candle:

1. EMA(30) is above EMA(144);
2. 120-bar price momentum is positive;
3. close is above SMA(240).

Otherwise hold cash. The completed-candle signal is acted on during the next
four-hour candle. The model is spot long/flat with no leverage and no shorting.
Base modeled all-in trading cost is 0.15% per side.

## Evidence and status

On the historical 2019-2026 comparison window, this rule produced approximately
+4,607% with a 1.48 Sharpe ratio and -34.8% maximum drawdown, versus +3,898%,
1.43, and -46.5% for the deployed rule. Those figures are post-selection:
the challenger was discovered after the full history had been examined.

The strategy must therefore remain paper-only and unchanged for at least 12
months. Historical results are not a promotion decision or a profit guarantee.

## Safety contract

- `LIVE_TRADING_APPROVED` is permanently false for this deployment.
- The account starts with fresh, isolated paper capital.
- No exchange-order credentials are used.
- The live signal extends a SHA-256-pinned 2017-2026 history so its recursive
  EMAs stay aligned with the audited rule.
- Market data must include an unbroken extension and a current candle.
- Repeated, stale, non-monotonic, or unreconciled-gap bars fail closed.
- Runtime state must never be copied from another strategy.
