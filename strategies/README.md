# Strategy suite

Each production candidate owns its frozen configuration, operating notes, and
runtime state here. Shared exchange and accounting code remains in `bot/`;
research and backtests remain in `src/`; generated evidence remains in
`reports/`.

| Strategy | Package | Status |
|---|---|---|
| Daily seven-vote spot ensemble | `daily_spot_ensemble/` | Primary paper benchmark |
| Four-hour dual-trend spot | `spot_4h_dual_trend/` | New independent paper candidate |
| Four-hour MA250 +2x/flat | `ma250_4h_long_flat/` | Restarted paper candidate |
| Four-hour MA250 +2x/-0.5x | `ma250_4h_long_short/` | Restarted paper candidate |

Runtime JSON and CSV files are deliberately separated by strategy. Never copy a
state file between strategies.
