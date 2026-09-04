# MA250 +2x long / -0.5x short

Paper-only BTCUSDT perpetual candidate.

- Completed close above SMA(250): target +2.0x.
- Otherwise: target -0.5x.
- Rebalances back to target once realised exposure drifts more than 10% away
  from it (`REBALANCE_BAND`). Fixed contract quantity otherwise lets leverage
  decay on winning trends and rise on losing ones.
- Uses perpetual candles, mark-price extremes, and real funding only.
- Prospective paper execution uses Bybit BTCUSDT linear-perpetual data through
  the authenticated Cloudflare relay because Binance blocks hosted CI traffic.
- Fails closed if perpetual, mark, or funding data cannot be reconciled.

The pre-fix one-trade ledger is retained under `archive/pre_fix_2026-07-28/`.
The corrected runtime restarts from a fresh account because the archived fill
was retroactive and the state was six bars stale.
