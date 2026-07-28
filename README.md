# BTC systematic-strategy suite

This repository contains four frozen BTC strategies, their independent paper
accounts, shared execution code, and reproducible research. All four are
paper-only. Historical performance is evidence for further testing, not a
promise of future profit.

| Strategy | Market | Audited return | CAGR | Sharpe | Max DD | Orders |
|---|---|---:|---:|---:|---:|---:|
| Daily seven-vote ensemble | Spot, 1d | +2,047.6% | 45.7% | 1.14 | -38.3% | 266 |
| Dual trend | Spot, 4h | +3,898.4% | 62.8% | 1.43 | -46.5% | 351 |
| MA250 +2x/flat | Perpetual, 4h | +4,971.6% | 76.9% | 1.17 | -63.9% | 315 |
| MA250 +2x/-0.5x | Perpetual, 4h | +6,981.5% | 85.7% | 1.23 | -57.4% | 316 |

The samples are not identical: perpetual history starts in September 2019.
Leveraged returns are not directly comparable with spot returns, and both
leveraged strategies have historically severe tail risk.

## Repository layout

```text
strategies/  frozen config, operating notes, and isolated runtime per strategy
bot/         shared paper runners, exchange adapters, notifications, persistence
src/         research, backtests, audits, and compatibility imports
reports/     generated audit tables and evidence
.github/     one daily and two four-hour paper workflows
worker/      read-only Telegram status interface
```

Start with [strategies/README.md](strategies/README.md) and
[the deployment audit](docs/audits/FOUR_STRATEGY_DEPLOYMENT_AUDIT.md).

## Paper commands

```bash
pip install -r requirements.txt
python bot/paper_bot.py status
python bot/paper_bot.py run --dry-run
python bot/paper_dual_4h_bot.py status
python bot/paper_dual_4h_bot.py run --dry-run
```

For the MA250 runner, set `FIXED_4H_VARIANT` to `long_flat` or `long_short`.

## Telegram

The read-only Telegram interface is documented in
[worker/README.md](worker/README.md). `/all` provides the four-account
dashboard; each strategy also has clearly named portfolio and trade commands.

## Safety contract

- Live trading is disabled in configuration, workflows, and Telegram.
- Runners fail closed if their required venue or fresh candle is unavailable.
- Every strategy owns separate state, status, and deduplicated trade records.
- Writes are atomic; failed workflow pushes fail visibly.
- Restarted four-hour accounts retain old records under `archive/`.

Do not enable real orders from historical results alone. Measure actual costs,
reconcile fills by exchange order ID, add exchange-side idempotency, and collect
at least 12 months of prospective paper evidence.
