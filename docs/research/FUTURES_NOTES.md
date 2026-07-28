# Leveraged Futures — Tested and Rejected (research notes)

Can `trend_ensemble` do better as a **leveraged perpetual-futures** strategy? We
tested it honestly — max **5x**, both long/flat and long/short, then ran the full
robustness battery on the best survivor. **Conclusion: no.** Leverage scales return
and risk together; it does not create risk-adjusted edge, and the drawdowns become
portfolio-ending. The locked **spot 1x** strategy stays the one we trade.

All results use the *identical* backtest parameters as the locked spot strategy —
same 7 trend votes, threshold 0.50, dates (2018-06-01 → now), 0.10% fee + 5 bps
slippage, daily bar, act-on-next-bar, no lookahead. The futures model adds the two
things spot ignores and leverage makes lethal:

- **Liquidation** on the intraday extreme — a long dies on the low, a short on the
  high, when `leverage × adverse_move` reaches `−(1 − maintenance)` (maint ≈ 0.5%).
- **Funding** paid daily on the leveraged notional (~11.7%/yr, measured from real
  Binance funding). Longs pay it; shorts receive it.

Reproduce: `python src/futures_5x.py` (the sweep) and
`python src/validate_futures_2x.py` (the battery).

---

## 1) Leverage sweep — caps 2x / 3x / 5x, long/flat (LF) and long/short (LS)

| Variant | Total Ret | CAGR | Sharpe | Max DD | Calmar | Outcome |
|---|--:|--:|--:|--:|--:|---|
| **Spot 1x (locked)** | +2,326% | 47.9% | **1.18** | **−38%** | 1.25 | baseline |
| Buy & hold 1x | +762% | 30.3% | 0.74 | −77% | 0.39 | — |
| **LF constant 2x** | +7,593% | 70.4% | **1.05** | −68% | **1.03** | best leveraged |
| LF voltarget 2x | +4,100% | 58.2% | 1.00 | −65% | 0.90 | survives |
| LF constant 3x | +7,091% | 69.0% | 1.05 | −87% | 0.79 | survives |
| LF voltarget 3x | +6,219% | 66.3% | 1.03 | −71% | 0.94 | survives |
| LF constant 5x | −100% | — | — | −100% | — | 💀 liquidated 2021-01-11 |
| LF voltarget 5x | +5,900% | 65.3% | 1.00 | −79% | 0.83 | survives |
| LS constant 2x | +497% | 24.5% | 0.78 | −89% | 0.28 | survives, weak |
| LS voltarget 2x | +960% | 33.6% | 0.77 | −85% | 0.39 | survives, weak |
| LS constant 3x | −83% | −19.7% | 0.78 | −99% | — | near-wipeout |
| LS voltarget 3x | +1,076% | 35.3% | 0.81 | −87% | 0.40 | survives, weak |
| LS constant 5x | −100% | — | — | −100% | — | 💀 liquidated 2020-03-13 |
| LS voltarget 5x | +780% | 30.6% | 0.78 | −89% | 0.34 | survives, weak |

Sizing definitions (all capped at the stated leverage):
- **LF constant** — full cap whenever the gate is long, flat otherwise.
- **LF voltarget** — `clip(target_vol / realized_vol, 0, cap)`, long/flat (vol target = 1.0).
- **LS** — same magnitude but shorts (`−cap`) when the votes disagree instead of going flat.

### What the sweep shows
- **2x is the sweet spot on the long side.** `LF constant 2x` has the best
  risk-adjusted profile of any leveraged variant (Sharpe 1.05, Calmar 1.03, maxDD
  never past −70%).
- **More leverage buys almost no extra return but a lot more drawdown.** Constant
  longs: −68% → −87% → *liquidated* as the cap goes 2x → 3x → 5x, and terminal
  return actually *falls* (deeper drawdowns + funding drag on the bigger notional).
  Vol-targeting is the only thing that keeps 5x alive at all.
- **Sharpe never beats spot 1x (1.18).** Every leveraged long sits at 1.00–1.05 —
  leverage scales return and risk together, no new edge. (Matches the earlier note
  in LOCKED.md: "2x/3x leave Sharpe unchanged ~1.03".)
- **Long/short is worse at every cap** (Sharpe 0.77–0.81, drawdowns −85% to −99%).
  The short side caught the 2022 bear (+69% in one variant) but **gave it all back
  in 2023 (−69%)** shorting into the recovery. Shorting fights BTC's upward drift
  and exposes you to squeeze-liquidations (a 5x short dies on a ~20% *rally*). The
  strategy's edge is *sitting flat* in downtrends, not betting against them.

---

## 2) Robustness battery on the best survivor — `LF constant 2x`

Same battery as `src/validate.py`, run through the futures model. The goal is to
break it.

| Test | Result | Verdict |
|---|---|---|
| Baseline | Sharpe **1.05**, maxDD −68%, CAGR 70% | — |
| Threshold 0.3–0.8 | Sharpe 0.89–1.05, maxDD −68% to −82% | ✅ > buy&hold, but 0.5 is a local *peak* |
| Window ±30% | Sharpe 0.91–1.05, maxDD −68% to −80% | ✅ degrades gracefully |
| Costs up to 5× | Sharpe 1.05 → 0.94 | ✅ survives |
| Exec lag +0…+3 bars | Sharpe 0.98–1.09 | ✅ no fragility |
| Bootstrap (2,000 × 30-day blocks) | Sharpe 5/50/95 = **0.46 / 1.05 / 1.65** | see below |

- **It survives** in the same qualitative sense the spot strategy does — the edge
  holds under every perturbation, and **82% of bootstrap paths beat buy&hold's
  Sharpe (0.74)**. The crash-avoidance signature persists levered (2022: −31% vs
  BTC −64%; lags strong bulls like 2023: +113% vs +156%).
- **But it does not rival spot 1x risk-adjusted:** only **35% of bootstrap paths
  beat spot's Sharpe of 1.18**. The extra CAGR is risk compensation, not edge.
- **The drawdowns are portfolio-ending:** baseline −68%, bootstrap **median −80%**,
  **5th-percentile −95%**. It never *liquidates* at 2x (needs a ~50% intraday drop),
  but surviving liquidation ≠ surviving a −80% drawdown. Spot's −38% is a different
  universe.
- **Mild overfit smell:** unlike spot's flat 0.3–0.8 threshold plateau, here 0.5 is
  a distinct *peak* (neighbors drop to ~0.89 Sharpe / −82% DD). Leverage makes the
  strategy more sensitive to the one knob.

---

## Verdict

`LF constant 2x` is a legitimate, non-overfit **aggressive variant** — ~3× the
terminal wealth of spot, same crash-avoidance DNA, robust to stress. But it buys
that return with roughly **double the drawdown and no risk-adjusted improvement**
over the locked spot 1x, and higher leverage (3x/5x) or shorting only makes it
worse. Same pattern as the rejected "binary" variant: more terminal wealth, not a
better edge.

**Decision: unchanged. Trade spot 1x** (`src/config.py`). These futures scripts are
research only — no live/paper wiring, and the locked config and docs are untouched.
