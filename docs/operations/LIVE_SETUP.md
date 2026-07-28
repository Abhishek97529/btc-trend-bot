# Live trading is intentionally disabled

The audited repository is paper-only. Workflows never submit orders, Telegram
cannot arm execution, and the daily configuration sets
`LIVE_TRADING_APPROVED = False`.

Live trading must not be restored until all of these are implemented:

1. Idempotency keys for every strategy/bar order.
2. Order-status and partial-fill reconciliation before state is committed.
3. Venue quantity, tick, minimum-notional, balance, and fee validation.
4. A tested kill switch and account-level maximum-loss limit.
5. Venue-specific fee, spread, funding, tax, and slippage modelling.
6. Alerts for stale data, missed bars, rejected orders, and state divergence.
7. At least 12 months of frozen-rule prospective paper results.
8. A separate, explicitly approved live deployment review.

Existing exchange-adapter code is not authorization to trade. Never store API
keys in the repository, logs, Telegram, or chat.
