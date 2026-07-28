# BTC strategy deployment audit

Audit date: 2026-07-28  
Scope: the three checked-in strategy accounts plus the proposed four-hour
dual-trend spot strategy.

## Bottom line

The historical trend effect is real enough to justify continued research, but
none of the checked-in bots is ready for live capital in its present form.

- **Best defensive strategy:** daily spot trend ensemble. It has the lowest
  historical drawdown and much less cost sensitivity than the four-hour model.
- **Best low-cost spot backtest:** four-hour dual trend. It has the best spot
  return and Sharpe, but it was selected using the same history, is
  turnover-sensitive, and is not deployed.
- **Highest historical return:** MA250 +2x/-0.5x perpetual. This is not the best
  risk-adjusted deployment choice. It is permanently exposed, depends on
  funding and liquidation accounting, and has very large simulated tail
  drawdowns.
- **Immediate deployment verdict:** keep all accounts paper-only. Repair the
  execution and persistence problems, then restart the prospective clock.

## Audited historical results

The periods differ because Binance perpetual history begins in September 2019.
These rows are therefore not a perfectly controlled head-to-head comparison.
Returns are before tax.

| Strategy | Market | Audited return | CAGR | Sharpe | Max DD | Order events |
|---|---|---:|---:|---:|---:|---:|
| Daily 7-vote ensemble | Spot, 1d | +2,047.6% | 45.7% | 1.14 | -38.3% | 266 |
| Dual trend | Spot, 4h | +3,898.4% | 62.8% | 1.43 | -46.5% | 351 |
| MA250 +2x/flat | Perpetual, 4h | +4,971.6% | 76.9% | 1.17 | -63.9% | 315 |
| MA250 +2x/-0.5x | Perpetual, 4h | +6,981.5% | 85.7% | 1.23 | -57.4% | 316 |

Important reconciliations:

- The daily locked headline of +2,326.4% is inflated by cutting the price data
  at 2018-06-01 before calculating the indicators. With the earlier Binance
  history retained for warm-up, the deployed-account replay is +2,047.6%.
- The frozen MA research simulator reports +4,994.0% and +7,021.8%. It trades
  first and assigns funding at that open to the new position. The deployed bot
  charges accumulated funding to the old position and then trades. Replaying
  the deployed order gives +4,971.6% and +6,981.5%. The numerical difference is
  small, but the research and deployment contracts must agree.
- The dual-trend vector result (+3,897.6%) and an exact fee-aware spot ledger
  (+3,898.4%) agree closely.

On a common reset starting 2019-09-08/09, the results were approximately
+983% daily, +1,468% dual trend, +4,972% MA long/flat, +6,982% MA long/short,
and +529% for unlevered BTC. The leveraged rows are not fair substitutes for
spot buy-and-hold: they use up to twice the directional exposure and accept
materially greater ruin risk.

## Robustness and regime evidence

### Daily ensemble

- Corrected pre-warm result remains far above buy-and-hold and retains a
  materially lower drawdown.
- It has only about 30 in-market spells. A few long BTC trends create nearly
  all wealth, so the effective sample is much smaller than the daily bar count.
- From 2025 onward it was approximately -1.5%, versus spot BTC near -30%.
- A 30-day block bootstrap produced roughly -45% median maximum drawdown and
  about -65% at the adverse fifth percentile.

### Dual-trend spot

- Base result: +3,898%, Sharpe 1.43, drawdown -46.5%, exposure 45.3%.
- It generated 176 entries and 175 exits. Only 31% of closed trades won; the
  ten largest winners account for approximately all terminal profit.
- A 108-configuration neighborhood remained broadly profitable over the whole
  sample, but the selected configuration sits near the favorable end.
- From 2023 onward, the selected rule returned about +244%, versus buy-and-hold
  about +295%. Only 3 of the 108 nearby configurations beat buy-and-hold in
  that later window.
- It did beat buy-and-hold from the separately examined 2023-07-15 boundary:
  about +153% versus +116%.
- At 0.30% total execution cost per side it still returned about +2,259%.
  At 0.45% it returned about +1,291%, below the normal-cost buy-and-hold
  benchmark. The estimated break-even cost for beating that benchmark was
  approximately 0.383% per side.
- Its 42-bar block bootstrap gave roughly -43% median maximum drawdown and -60%
  at the adverse fifth percentile. This resamples the same historical regimes;
  it is not independent evidence.

### MA250 perpetual accounts

- Both rules remained profitable under parameter, extra-delay, higher-cost,
  and higher-funding stresses.
- Long/flat passed 7 of 8 pre-paper gates. It failed the tail-drawdown gate:
  41.7% of one-week-block bootstrap paths had a drawdown worse than 70%.
- Long/short had a similar 42.1% probability of a drawdown worse than 70% and
  an extended spot-proxy historical drawdown of about 88.5%.
- A synthetic 50% adverse mark-price shock liquidated the +2x account. No
  liquidation in the observed history is not a guarantee.
- From 2025 onward, long/flat lost about 30.6% and long/short lost about 25.0%.
  Their full-history numbers are dominated by 2020-2024.

## Deployment findings

### Daily spot bot

1. **Critical if live is armed — duplicate-order risk.** The real order is
   submitted before live metadata is saved. A crash after exchange acceptance
   can repeat the same bar because no client order ID or confirmed fill record
   is persisted.
2. **High — state persistence can fail silently.** The workflow retry loop can
   end successfully after its final failed push, leaving the next run with old
   state.
3. **High — no freshness or catch-up contract.** Equality is the only bar
   guard. Older data can move `last_bar` backward, and missed bars are skipped
   rather than reconciled.
4. **High — paper fills are retroactive.** The account fills at the previous
   daily close even when the workflow actually runs hours later. A 04:00 UTC
   fill stress reduced the corrected result from roughly +2,051% to +1,945%;
   the edge survived, but the paper ledger is not an honest fill ledger.
5. **High — full allocation does not reserve fees.** A $10,000 full buy in the
   current accounting leaves -$15 cash and 100.15% exposure. Historical replay
   produced 44 negative-cash events.
6. **Medium — fallback venue is not recorded.** A Binance failure can silently
   switch the signal source to Coinbase while status omits source and gap
   diagnostics.

The current full-history signal and the bot's 290-day window agree: 1 of 7
votes is up and the target is cash. Across the observed sample, the 290-day
window produced no signal mismatches, although persisting the stateful
Donchian vote or fetching full history would make the contract explicit.

### Both four-hour perpetual bots

1. **Blocker — prospective records are stale.** At audit time both checked-in
   states were six completed four-hour candles behind and contained only one
   entry. No useful prospective performance record exists yet.
2. **High — unavailable historical fills.** The process uses the current
   candle's already-passed opening price. The first stored entry ran 3 hours
   53 minutes after its recorded fill.
3. **High — missed liquidation path.** Only the latest completed candle is
   checked. A synthetic gap test liquidated on an omitted earlier candle while
   the bot incorrectly survived because the latest candle was safe.
4. **High — funding/liquidation chronology is wrong.** Liquidation is checked
   before accumulated funding is deducted, and several settlements are priced
   using one later price. A synthetic test showed funding can push equity below
   maintenance after the only liquidation check.
5. **High — fail-open market substitution.** Any perpetual or mark-price
   failure switches to spot candles and still allows state mutation. Funding
   failure substitutes a fixed positive mean rate, which can reverse the
   economic sign for shorts.
6. **Medium — state, status and ledger writes are non-atomic.** A partial write
   can advance the bar while leaving the evidence incomplete. Workflow push
   failure can also be masked as success.
7. **Low but real — exposure is slightly overstated after fees.** A nominal
   +2x entry becomes about +2.0028x. The displayed target also hides the
   effective leverage drift caused by fixed contract quantity.

## Venue-cost warning

The base spot tests assume 0.10% trading fee plus 0.05% slippage per side.
[CoinDCX's published fee schedule](https://coindcx.com/fees/) currently lists
regular-tier INR spot fees as high as 0.50%, with GST on fees. Adding 0.05%
slippage gives roughly 0.64% per side.
At that cost, the dual-trend replay returned only about +616%, versus
buy-and-hold near +1,664% over its sample. It therefore fails its stated
outperformance objective on a regular-tier INR venue.

CoinDCX's published crypto-to-crypto spot fee is lower (0.17% before GST); with
the same slippage, a roughly 0.25%-per-side test returned about +2,715%, still
above buy-and-hold. The actual account tier, pair, GST, spread, TDS cash-flow,
and tax treatment must be put into the model before selecting a strategy.
CoinDCX also exposes order-status and trade-update interfaces in its
[official API reference](https://docs.coindcx.com/); the live path should use
them to reconcile accepted and partially filled orders before advancing state.

## Decision

1. Keep the daily account as the primary defensive paper benchmark, but disable
   real execution until order idempotency, fee-aware sizing, freshness checks,
   actual fill reconciliation, and durable state commits are fixed.
2. Pause and restart both MA250 prospective tests after repairing fill timing,
   missed-bar replay, funding/liquidation order, fail-closed data handling, and
   atomic persistence.
3. Add dual trend only as a separate fourth paper account and only on a venue
   whose measured all-in cost is below approximately 0.38% per side. Do not
   replace the daily account.
4. Freeze every rule and require at least 12 months of prospective fills before
   comparing live candidates. Historical optimization and the current handful
   of runs are not forward evidence.

The detailed common-period yearly return, drawdown, and order counts are in
`reports/four_strategy_common_yoy.csv`.
