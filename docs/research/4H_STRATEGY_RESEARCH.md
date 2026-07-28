# 4-Hour BTC Strategy Research — Swing, Spot vs Leverage, and Real-Perp Walk-Forward

**Author:** research session, 2026-07-27
**Instrument:** BTC/USDT, 4-hour timeframe
**Status:** RESEARCH ONLY. The production **LOCKED daily spot `trend_ensemble`** strategy
(`src/config.py`, `LOCKED.md`) was **not** modified. Nothing here is deployed.

> **One-line takeaway:** Yes, profitable 4h trend strategies exist and beat buy-and-hold,
> but every "amazing" leveraged number collapses under honest forward-testing. The real,
> out-of-sample answer is **~32% CAGR at 2× with a −66% drawdown** — and **leverage adds
> return, not edge** (Sharpe is unchanged). **Do not over-optimize; the simplest rule wins.**

---

## 0. How to reproduce

| Script | What it does |
|---|---|
| `src/swing_4h_study.py` | 4h swing survey vs B&H, full-sample + one OOS split |
| `src/momentum_4h_report.py` | `momentum(120,300)` deep-dive + year-by-year |
| `src/compare_locked_vs_momentum.py` | LOCKED daily spot vs 4h momentum, same window |
| `src/lev_4h_study.py` | Leverage engine (funding + intrabar liquidation), leverage sweep |
| `src/lev_best_4h.py` | Ranks bases levered, picks winner, OOS + YoY |
| `src/best_2x_search.py` | Grid search at 2×, winner chosen out-of-sample |
| `src/fetch_futures.py` | Downloads **real Binance perpetual** 4h OHLCV + mark price |
| `src/perp_walkforward.py` | Full pipeline on real perp: regression + walk-forward + optimize |

Cost model: spot studies 0.10% fee + 5bps slippage/side; perp studies 0.04% fee + 3bps/side.
4h bars/year = 2190. No lookahead (engine executes `target.shift(1)`). Funding = real Binance
8h data (~11.7%/yr drag on longs). Maintenance margin 0.5%. Data: 2019→2026-07.

---

## 1. Do any 4h swing (spot, no leverage) strategies beat buy & hold?

**Yes — the slow trend-followers do, and their real edge is risk reduction.**

Full sample (2019→2026), B&H = +1,686% net, Sharpe 0.93, maxDD **−77%**.

| Strategy (4h, spot) | Net % | CAGR | Sharpe | maxDD |
|---|--:|--:|--:|--:|
| momentum(120,300) | +3,121% | 58% | **1.35** | −55% |
| trend_vol_target | +2,033% | 50% | 1.26 | **−38%** |
| ma_regime(200) | +2,028% | 50% | 1.17 | −46% |
| *buy & hold* | *+1,686%* | *46%* | *0.93* | *−77%* |

**Out-of-sample** (train <2023-07-15, test after), B&H test = +118%, Sharpe 0.79:
- `ema_crossover(24,168)`: **+153%**, Sharpe 1.14, maxDD −29% — beat B&H on return *and* halved drawdown.
- `ma_regime(250)`: +118%, Sharpe 0.98 — matched return, better risk.
- 2 of 5 families beat B&H on **raw** return OOS; **nearly all** beat it risk-adjusted.

**What works:** slow trend-following (EMA/MA-regime/long-lookback momentum).
**What fails:** fast crossovers, short Donchian, RSI mean-reversion (chewed up by turnover).

---

## 2. `momentum(120,300)` — is it leverage? No.

It is **spot long/flat, binary {0,1}, no leverage, no shorts, no funding.**
Rule (4h bars): hold 100% BTC when *(20-day ROC > 0)* **and** *(price > 50-day MA)*, else 100% cash.

Full sample: **+3,121% / CAGR 58% / Sharpe 1.35 / maxDD −55% / 46% time in market / 411 trades.**
₹1L → ₹32.2L (vs B&H ₹17.9L).

**Year-by-year:**

| Year | Strat % | B&H % | In-mkt | maxDD |
|---|--:|--:|--:|--:|
| 2019 | +110 | +95 | 47% | −36 |
| 2020 | +294 | +302 | 61% | −21 |
| 2021 | +106 | +60 | 51% | −26 |
| 2022 | **−47** | **−64** | 20% | −48 |
| 2023 | +133 | +156 | 56% | −15 |
| 2024 | +54 | +121 | 59% | −27 |
| 2025 | +8 | −6 | 36% | −17 |
| 2026* | −9 | −25 | 36% | −20 |

Beat B&H in 5/8 years. Wins in bear/choppy years (dodges drawdowns); lags in V-shaped recoveries.

---

## 3. 4h momentum vs our LOCKED daily spot strategy (same window)

| Metric | 🔒 LOCKED daily | 4h momentum(120,300) | Buy & Hold |
|---|--:|--:|--:|
| Total return | +2,326% | **+3,121%** | +1,605% |
| CAGR | 52% | **58%** | 46% |
| Sharpe | 1.2 | **1.3** | 0.9 |
| Sortino | **1.4** | 1.2 | 1.2 |
| Max drawdown | **−38%** | −55% | −77% |
| Calmar | **1.4** | 1.1 | 0.6 |
| Trades | **241** | 411 | 0 |
| ₹1L becomes | ₹24.3L | **₹32.2L** | ₹17.1L |

**Verdict:** 4h momentum wins raw return; LOCKED daily wins everything protective (drawdown,
Calmar, Sortino, **half the turnover**). Under Indian tax (30%, no loss offset, per winning trade)
+ real fees, the daily strategy's lower turnover makes it more tax-efficient. **No good reason to
abandon the LOCKED daily strategy.**

---

## 4. Leverage — done honestly (funding + liquidation modeled)

Most 4h-leverage backtests ignore the two things that kill accounts: **funding** and
**liquidation**. This engine models both. Key structural finding:

> **Trend-gated leverage NEVER liquidated in 7.5 years — at any leverage, even 5×** —
> because the trend filter sits in **cash during crashes**, so you're not holding leveraged
> when BTC prints a −20%+ intrabar candle.

Momentum(120,300) base, leverage sweep (full sample):

| Config | Net % | CAGR | Sharpe | maxDD | Liq? |
|---|--:|--:|--:|--:|:--|
| L=1 | +2,185% | 51% | 1.23 | −55% | none |
| L=2 | +15,417% | 95% | 1.23 | −82% | none |
| L=3 | +30,850% | 113% | 1.23 | −93% | none |
| L=5 | +2,637% | 55% | 1.23 | **−99.7%** | none |

**Two hard lessons:**
1. **Leverage does not improve Sharpe** (identical 1.23 at every level). It slides you along the
   *same* risk/return line — more return bought with proportional drawdown.
2. **Volatility decay caps useful leverage at ~3×.** L=5 earns *less* than L=3 and nearly zeroes
   out. Past ~2–3× you pay in drawdown and get nothing back.

---

## 5. "Best" leveraged strategy — and why the answer kept changing

- Ranked full-sample by Sharpe → winner `ma_regime(250) ×2` (Sharpe 1.26, best Calmar).
- Selected **out-of-sample** (tune on train, judge on test) → winner **`ema_crossover(36,144) ×2`**
  (test Sharpe 1.06, the only one comfortably >1; test CAGR ~60%, test maxDD −53%).

The two methods disagreeing was the **red flag** that these single-split "winners" are unstable —
which §6 confirmed decisively.

---

## 6. THE DEFINITIVE TEST — real Binance perp, walk-forward

Downloaded **real perpetual futures** (`src/fetch_futures.py`): 15,081 4h bars + mark price +
real funding. Ran regression, a rolling **walk-forward** (18mo train → 6mo test, params chosen
only on past data), and optimization.

### In-sample looks amazing; forward test does not

| | CAGR | Sharpe | maxDD |
|---|--:|--:|--:|
| Regression (in-sample) — ma_regime(250) 2× | **97%** | 1.24 | −66% |
| **Walk-forward (re-optimized, true OOS)** | **15%** | 0.54 | −65% |

The 97% is a **mirage** — inflated by the 2019–21 bull inside the fit. Honest forward test:
₹1L → ₹2.15L over 5.4 years (2.2×), −65% drawdown.

### Re-optimizing HURT — the key optimization lesson

Same OOS window (2021-03 → 2026-07), 2×, real perp:

| Strategy (fixed, no re-optimizing) | CAGR | Sharpe | maxDD |
|---|--:|--:|--:|
| **ma_regime(250) ×2** | **31.8%** | **0.75** | −66% |
| **mom(180,300) ×2** | **32.3%** | **0.76** | −62% |
| ema(36,144) ×2 | 16.6% | 0.57 | −77% |
| *Re-optimized walk-forward* | *15.3%* | *0.54* | *−65%* |
| ma_regime(250) **×1 spot** | 21.8% | 0.75 | **−39%** |
| Buy & hold ×1 | 4.1% | 0.35 | −77% |

**Fixing one simple rule ~doubled the re-optimized CAGR at a better Sharpe.** The walk-forward's
per-window winners kept rotating (each config won only 1–2 of 11 folds; train Sharpe 2.2 → test
−1.6 in one fold). **The more you optimize a 4h leveraged strategy, the worse it gets.**

### Walk-forward year-by-year (real OOS)

| Year | WF ret | WF DD | B&H |
|---|--:|--:|--:|
| 2021 | +10 | −54 | −14 |
| 2022 | −54 | −56 | −64 |
| 2023 | +163 | −40 | +156 |
| 2024 | +110 | −50 | +121 |
| 2025 | −11 | −39 | −6 |
| 2026* | −13 | −35 | −26 |

---

## 7. Final conclusions

1. **Profitable 4h trend strategies exist and beat B&H** — but the edge is risk-adjusted
   (drawdown reduction), and it's specifically *slow* trend-following.
2. **The best 2× perp strategy, forward-tested, is `ma_regime(250) ×2` or `mom(180,300) ×2`:**
   ~**32% CAGR, Sharpe 0.75, −62 to −66% drawdown**, no liquidations. Both crushed B&H
   (4% CAGR) over the real OOS window.
3. **Leverage is a risk dial, not an edge.** 1× and 2× have the *same* Sharpe (0.75); 2× just
   deepens the drawdown (−39% → −66%) for more upside. Defensible only if you truly bear −66%.
4. **Do not optimize.** Re-optimizing every window *reduced* OOS return. Pick the simplest robust
   rule (250-bar MA regime) and leave it alone. In-sample CAGR (97%) is ~3× the honest OOS (32%).
5. **For this user specifically:** a 4h leveraged perp strategy needs **offshore perps** (Indian
   crypto-derivatives tax/legal is murky and harsh), trades far more than the daily spot strategy
   (worse under India's no-offset 30% tax), and is *lower risk-adjusted* than the LOCKED daily
   spot strategy with a much uglier ride. **The LOCKED daily spot strategy remains the recommended
   production system.**

---

## 8. Honest caveats / limitations

- Single-asset (BTC), single history (2019→2026) that includes only ~1.5 major cycles — small
  sample for a 4h strategy despite the bar count.
- Walk-forward uses one train/test cadence (18/6mo); other cadences would shift specifics, not the
  conclusion (re-optimization is fragile).
- Perp fees assumed 0.04%+3bps (Binance-ish); real fills, funding spikes, and exchange risk are
  worse. Tax not applied in the return figures.
- "No liquidation in 7 years" is history, not a guarantee — a fast enough intrabar wick while
  levered and in-position would still liquidate. Mark-price gaps modeled bar-to-bar, not tick.
- Nothing here overrides the LOCKED strategy or is production-ready. Live perp trading would need a
  separate spec: venue, margin/kill-switch rules, and a real paper track record first.
