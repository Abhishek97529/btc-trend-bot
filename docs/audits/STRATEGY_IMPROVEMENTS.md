# Strategy improvement study

Date: 2026-09-04. Reproduce with:

```bash
python src/dual_trend_improvements.py   # spot 4h dual trend overlays
python src/ma250_risk_study.py          # leveraged MA250 overlays
```

All variants are evaluated on the same 4h BTCUSDT history
(`data/BTCUSDT_4h_2017-08-17_2026-07-27.parquet`), the same frozen costs, and
the same next-open execution timing, so differences come from the rule rather
than the harness.

These are **backtests on the same history the original rules were selected
from**. They are a reason to test a change prospectively, not a reason to
believe the numbers.

## Headline

Two findings.

**For risk:** volatility targeting with a rebalance band. Scale exposure by
`target_vol / realised_vol` instead of holding a constant size, and only resize
when the change exceeds a band. This helps both strategy families.

**For profit:** cheaper execution, not a better signal. Maker-side fills are
worth roughly 35% more terminal profit on the perpetual account (section 3).
Every signal or leverage change tested either lost money or bought its return
with risk.

## 1. Spot dual trend

| Variant | Return % | CAGR % | Sharpe | Sortino | Max DD % | Trades | Calmar |
|---|---:|---:|---:|---:|---:|---:|---:|
| Buy and hold | 1,409 | 35.5 | 0.78 | 0.99 | -83.9 | 1 | 0.42 |
| **Baseline (frozen)** | **7,559** | **62.5** | **1.35** | **1.15** | **-59.2** | **409** | **1.05** |
| Trailing ATR 4.0x | 5,671 | 57.4 | 1.28 | 1.10 | -55.8 | 557 | 1.03 |
| Vol target 50, no band | 3,277 | 48.2 | 1.41 | 1.24 | -44.6 | 4,416 | 1.08 |
| **Vol target 50, band 20%** | **3,609** | **49.8** | **1.42** | **1.25** | **-42.7** | **525** | **1.17** |
| Regime slope filter | 5,938 | 58.2 | 1.34 | 1.03 | -57.1 | 301 | 1.02 |

The band matters more than the target. Naive vol targeting rebalances nearly
every bar and lifts turnover from 409 to 4,416 trades; a 20% band delivers
slightly better risk numbers with 525 trades. Turnover, not the risk model, is
what would have killed this overlay in production.

Trailing stops did not pay for themselves. They raise turnover and cut return
without improving Sharpe, because the frozen exit already leaves on trend loss.

### Cost stress, total return %

| Variant | 0.15% | 0.25% | 0.38% | 0.64% |
|---|---:|---:|---:|---:|
| Baseline | 7,559 | 4,985 | 2,884 | 925 |
| Vol target 50 | 3,277 | 2,082 | 1,137 | 296 |
| Vol target 50, band 20% | 3,609 | 2,409 | 1,408 | 444 |
| Regime slope filter | 5,938 | 4,366 | 2,916 | **1,274** |

The **regime slope filter is the most cost-robust variant** and the only one
that beats the baseline at 0.64% per side. If the spot venue turns out to be
CoinDCX INR regular tier, that is the overlay to prefer; on a cheap venue the
banded vol target gives better risk-adjusted returns.

## 2. Leveraged MA250

Spot candles stand in for perpetual marks (the audit's extended spot-proxy
method), so returns run higher than the 2019-start perpetual audit. The tail
comparison between rows is the point, not the absolute return.

| Variant | Return % | CAGR % | Sharpe | Sortino | Max DD % | P(DD worse than 70%) |
|---|---:|---:|---:|---:|---:|---:|
| MA250 1x | 3,864 | 50.9 | 1.12 | 1.04 | -73.3 | 14.0% |
| MA250 1.5x | 11,875 | 70.8 | 1.12 | 1.04 | -87.7 | 68.3% |
| **MA250 2x (frozen)** | **21,938** | **82.8** | **1.12** | **1.04** | **-94.8** | **96.0%** |
| Vol target 60, cap 2.0 | 6,840 | 60.7 | 1.22 | 1.21 | -67.1 | 25.3% |
| Vol target 50, cap 2.0 | 4,339 | 52.8 | 1.22 | 1.21 | -63.3 | 8.3% |
| **Vol target 50, cap 1.5** | **3,500** | **49.3** | **1.20** | **1.19** | **-60.0** | **6.7%** |

Two things stand out.

**Constant leverage buys return with pure risk, not skill.** Sharpe is
identical at 1x, 1.5x, and 2x (1.1155) because constant leverage scales the
return stream linearly. The only thing 2x adds is drawdown: -73% to -95%, and
the probability of a worse-than-70% drawdown rises from 14% to 96%.

**Vol targeting is the only variant that improves Sharpe at all**, from 1.12 to
1.22, and it improves Sortino much more (1.04 to 1.21) because it is cutting
specifically the downside.

### The cap decides liquidation survival

| Shock | 2x frozen | Vol tgt cap 2.0 | Vol tgt cap 1.5 |
|---|---|---|---|
| -30% | survives | survives | survives |
| -40% | survives | survives | survives |
| -50% | **LIQUIDATED** | **LIQUIDATED** | survives |

Vol targeting lowers *average* exposure and tail drawdown, but a 2.0 cap can
still be at 2.0 when a shock lands. Only the 1.5 cap survives -50%. If
liquidation avoidance is the objective, **the cap is the control that matters**,
not the average.

### It holds in every regime

Max drawdown %, so less negative is better:

| Period | 2x frozen | Vol tgt cap 2.0 | Vol tgt cap 1.5 |
|---|---:|---:|---:|
| 2018 bear | -86.6 | -53.5 | **-50.1** |
| 2019-2020 | -76.5 | -38.4 | -38.9 |
| 2021 bull | -53.0 | -27.7 | -27.7 |
| 2022 bear | -64.5 | -40.8 | -39.3 |
| 2023-2024 | -38.9 | -32.4 | -28.1 |
| 2025 on | -55.8 | -40.8 | -39.9 |

Drawdown improves in **every period without exception**, and losing periods
lose materially less: 2018 -79.9% to -46.1%, 2022 -59.1% to -33.4%, 2025 -42.4%
to -20.9%. A single tuned parameter usually helps in some regimes and hurts in
others; consistency across all six is the main reason to take this seriously.

## 3. The one lever that raises return without raising risk

Signal tuning on this history is curve-fitting, and leverage buys return with
pure risk (section 2). Execution cost is the remaining lever: it raises terminal
profit while leaving the return *stream* — and therefore the risk profile —
unchanged.

Reproduce with `python src/execution_cost_upside.py`.

| Strategy | Current | Cheaper execution | Uplift |
|---|---:|---:|---:|
| MA250 +2x perp | 21,938% @ taker 0.055% | **29,711% @ maker 0.020%** | **+35.4%** |
| Vol-targeted challenger | 3,500% @ taker | **4,304% @ maker** | **+23.0%** |
| Spot dual trend | 7,559% @ 0.100% | **8,384% @ 0.075% (BNB)** | **+10.9%** |

A 3.5 bps per-side saving compounds into 35% more terminal profit on the
perpetual account over the sample. That is a larger, safer gain than anything
the parameter search produced.

The catch is fill risk. Posting passive limit orders instead of crossing the
spread means some orders do not fill, and a trend follower that misses an entry
on a 4h bar can miss the move that pays for the year. The uplift above assumes
every maker order fills, which is the optimistic bound, not the expected value.
A realistic implementation would post passive with a timeout and cross on
expiry, capturing part of the spread rather than all of it.

## Recommendation

1. **Add one vol-targeted variant per family as a new paper account.** Do not
   edit the frozen configs; the bake-off is only meaningful if the incumbents
   keep running unchanged.
2. **Prefer a 1.5x cap over 2.0x** for any leveraged candidate. The extra 0.5x
   contributed no Sharpe, and it is the difference between surviving and being
   liquidated by a 50% shock.
3. **Settle the spot venue first.** It decides whether the banded vol target
   (cheap venue) or the regime slope filter (expensive venue) is the right
   overlay, and whether the spot strategies are viable at all.
4. **Test maker execution before tuning anything else.** It is the only change
   found that raises profit without raising risk, and it is worth more than any
   parameter change tested. Measure the actual fill rate on a 4h bar first.
5. Judge all of this on prospective fills. Every number above is in-sample.

## What was implemented

Recommendation 1 and 2 are done for the leveraged family. A sixth paper account,
[`ma250_4h_voltarget`](../../strategies/ma250_4h_voltarget/README.md), runs the
same SMA(250) direction rule sized by `VOL_TARGET / realised_vol` with a 1.5x
cap and a 20% rebalance band. It is wired into the existing four-hour scheduler
and the Telegram interface (`/mavol`, `/mavol_trades`).

The two incumbent MA250 configs were left at constant +2.0x on purpose, and a
regression test asserts they have no `VOL_TARGET` so their sizing cannot drift
from the frozen contract. The bake-off stays honest.

The spot overlays in section 1 were **not** deployed. Which one is correct
depends on the unresolved venue cost, so freezing one now would be guessing.
