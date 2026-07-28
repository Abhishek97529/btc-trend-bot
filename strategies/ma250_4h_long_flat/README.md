# MA250 +2x long / flat

Paper-only BTCUSDT perpetual candidate.

- Completed close above SMA(250): target +2.0x.
- Otherwise: flat.
- Uses perpetual candles, mark-price extremes, and real funding only.
- Fails closed if perpetual, mark, or funding data cannot be reconciled.

The pre-fix one-trade ledger is retained under `archive/pre_fix_2026-07-28/`.
The corrected runtime restarts from a fresh account because the archived fill
was retroactive and the state was six bars stale.
