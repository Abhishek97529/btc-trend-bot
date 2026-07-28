# Strategy-runtime migration — 2026-07-28

- The daily paper account moved intact to
  `strategies/daily_spot_ensemble/runtime/`.
- Unreliable MA250 records moved to each package's
  `archive/pre_fix_2026-07-28/` directory.
- Both MA250 accounts restarted flat under corrected execution, funding,
  liquidation, freshness, and persistence rules.
- Dual-trend spot received a new independent paper account.

Archived records remain for auditability and must not be joined to restarted
prospective records.
