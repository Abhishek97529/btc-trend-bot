# Corrected audit — locked BTCUSDT daily spot ensemble

Audit date: 2026-07-28  
Verdict: retain as the primary defensive paper benchmark; do not run live yet.

## Correction to the locked headline

The locked +2,326.37% result is not a valid reproduction of the deployed
strategy. The old research loader cut the dataset at 2018-06-01 before
calculating SMA, EMA, momentum, and stateful Donchian indicators. That cold
start forced the strategy flat during part of 2018 and omitted a real loss.

Using all earlier Binance history for indicator warm-up, beginning the account
on 2018-06-01, trading at the next daily open, applying the 1% dust rule, and
replaying the deployed pre-cost sizing gives:

| Metric | Corrected deployed replay |
|---|---:|
| Total return | +2,047.58% |
| CAGR | 45.69% |
| Sharpe | 1.136 |
| Maximum drawdown | -38.27% |
| Material rebalance orders | 266 |
| Entries from cash | 30 |

A fee-aware full-allocation solver gives +2,050.73%, CAGR 45.71%, Sharpe 1.136,
and drawdown -38.16%. The small difference confirms that the trend result
survives the accounting correction, but the deployed negative-cash behavior
still needs fixing.

## Year by year

Fee-aware exact spot ledger, full prehistory, 1% dust rule. 2018 begins June 1
and 2026 ends July 23.

| Year | Return | Max DD | Orders |
|---:|---:|---:|---:|
| 2018* | -11.57% | -11.57% | 8 |
| 2019 | +120.13% | -28.42% | 29 |
| 2020 | +164.63% | -25.99% | 37 |
| 2021 | +72.57% | -32.25% | 43 |
| 2022 | -11.53% | -12.10% | 6 |
| 2023 | +65.52% | -22.19% | 39 |
| 2024 | +67.52% | -32.53% | 45 |
| 2025 | +4.18% | -16.09% | 49 |
| 2026* | -5.33% | -7.52% | 10 |

## Implementation audit

- Current full-history and 290-day-window signals match: 1 of 7 votes is up,
  target exposure is zero, and the stored account is flat.
- The 290-day window produced no signal mismatch in the observed historical
  replay. Persisting the Donchian state or loading complete history would still
  remove a latent path-dependence risk.
- Full-buy sizing spends 100% of pre-fee equity and then deducts costs. A
  $10,000 test leaves -$15 cash and 100.15% exposure; 44 negative-cash events
  occurred historically.
- Paper mode records the previous close as its fill even if the workflow runs
  hours later. A 04:00 UTC delayed-fill stress returned about +1,945%, rather
  than +2,051%.
- The live order and idempotency state are not transactional. A crash after an
  accepted CoinDCX order but before metadata persistence can duplicate the
  order on the next run.
- Workflow push retries can exhaust without failing the job, leaving stale
  state in the repository.
- Bar freshness is not enforced and skipped bars are not replayed.
- A Binance data failure can silently switch the signal to Coinbase without
  recording that source in status.

## Cost sensitivity

With correct prehistory and fee-aware sizing:

| Total execution cost per side | Return | Sharpe | Max DD |
|---:|---:|---:|---:|
| 0.15% | +2,050.7% | 1.14 | -38.2% |
| 0.30% | +1,849.3% | 1.11 | -38.8% |
| 0.45% | +1,666.8% | 1.08 | -39.4% |
| 0.64% | +1,459.9% | 1.04 | -40.1% |
| 0.80% | +1,304.5% | 1.00 | -40.8% |

This lower-turnover strategy remains historically profitable at regular-tier
CoinDCX-like INR costs, unlike the four-hour dual-trend candidate. Tax, TDS
cash-flow, spread, custody, and venue basis remain excluded.

## Decision

Keep the rule frozen and paper-only. Before live use, implement exchange-safe
quantity sizing, current executable paper fills, monotonic/fresh bar checks,
gap replay, client-order idempotency, confirmed-fill logging, atomic state
persistence, and a workflow that fails if state cannot be pushed.

At least 12 months of clean prospective reconciliation is required. Historical
45.7% CAGR is not a forward expectation.
