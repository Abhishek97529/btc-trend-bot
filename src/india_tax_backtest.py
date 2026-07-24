"""
AFTER-TAX reality check for the LOCKED trend_ensemble strategy under Indian
crypto-tax rules (Section 115BBH + 194S), as they stand for FY2025-26.

This is an ADDITIVE overlay — it does NOT touch config.py or the strategy. It
re-runs the exact locked backtest, then layers on:

  * 30% flat tax on gains from Virtual Digital Assets (VDA)  [+ 4% cess ≈ 31.2%]
  * NO set-off: a loss on one trade CANNOT offset a gain on another (or any
    other income). So annual tax = 30% x SUM OF WINNING trades only.
  * 1% TDS on every SELL (Section 194S). This is CREDITABLE against your final
    tax bill — it's a cash-flow drag, not a permanent cost — so it's reported
    separately, not subtracted from the terminal wealth comparison.
  * Indian financial year = 1 Apr -> 31 Mar. Tax on a year's net winners is
    modelled as paid at year end (deducted from the account, so it stops
    compounding — the realistic self-funded case).

A "trade" for tax = one in-market SPELL (buy on entry, sell on exit), matching
the trade-by-trade log in locked_report.py. Fractional rebalances inside a spell
are treated as one position held to exit (the realistic hold-to-exit case; daily
micro-rebalancing would create hundreds of tiny taxable events and only make the
active strategy look WORSE, so this is the charitable assumption).

The decisive comparison is active-after-tax vs BUY & HOLD-after-tax: buy&hold
has ONE taxable event at the very end, so it dodges both the no-offset penalty
and the compounding drag of annual tax. India's regime structurally rewards it.

Usage:  python src/india_tax_backtest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C
from strategies_v2 import trend_ensemble
from backtest import run_backtest
import metrics as M

DATA = Path(__file__).resolve().parent.parent / "data" / "BTCUSDT_1d_2017-08-01_2026-07-23.parquet"

TAX_RATE = 0.30      # 115BBH flat rate on VDA gains (cess extra; see EFFECTIVE)
CESS = 0.04          # health & education cess on the tax
EFFECTIVE = TAX_RATE * (1 + CESS)   # ≈ 0.312
TDS_RATE = 0.01      # 194S, on every sell; creditable


def load():
    df = pd.read_parquet(DATA)
    return df[~df.index.duplicated()].sort_index().loc[C.BACKTEST_START:]


def fy_of(ts: pd.Timestamp) -> str:
    """Indian financial year label for a date: Apr..Mar."""
    y = ts.year
    start = y if ts.month >= 4 else y - 1
    return f"FY{start}-{str(start + 1)[-2:]}"


def simulate_after_tax(ret: pd.Series, pos: pd.Series, effective_rate: float):
    """Walk the equity curve day-by-day, book per-spell realized gains, and pay
    tax at each financial-year boundary on the SUM OF WINNING spells only
    (no loss offset). Returns (terminal_equity, per_fy_rows, tds_total, spell_pnls)."""
    idx = ret.index
    E = C.INITIAL_CAPITAL
    in_mkt = pos.values > 1e-9

    fy_winners: dict[str, float] = {}   # sum of POSITIVE spell gains, by exit FY
    fy_all: dict[str, float] = {}       # net (winners+losers), by exit FY — for the "if offset allowed" case
    spell_pnls: list[float] = []
    tds_total = 0.0

    cur_fy = fy_of(idx[0])
    fy_tax_paid: dict[str, float] = {}
    spell_gain = 0.0
    spell_peak_notional = 0.0

    def close_fy(fy: str):
        nonlocal E
        win = fy_winners.get(fy, 0.0)
        tax = effective_rate * win if win > 0 else 0.0
        fy_tax_paid[fy] = tax
        E -= tax

    for t in range(len(idx)):
        # financial-year rollover -> settle the year that just ended
        this_fy = fy_of(idx[t])
        if this_fy != cur_fy:
            close_fy(cur_fy)
            cur_fy = this_fy

        r = float(ret.iloc[t])
        pnl = E * r
        E += pnl
        if in_mkt[t]:
            spell_gain += pnl
            spell_peak_notional = max(spell_peak_notional, E * float(pos.iloc[t]))

        # spell just ended? (in market at t, flat at t+1 or end of series)
        ended = in_mkt[t] and (t + 1 >= len(idx) or not in_mkt[t + 1])
        if ended:
            spell_pnls.append(spell_gain)
            exit_fy = fy_of(idx[t])
            fy_all[exit_fy] = fy_all.get(exit_fy, 0.0) + spell_gain
            if spell_gain > 0:
                fy_winners[exit_fy] = fy_winners.get(exit_fy, 0.0) + spell_gain
            # 1% TDS on the sell notional (value of BTC sold at exit); creditable.
            tds_total += TDS_RATE * spell_peak_notional
            spell_gain = 0.0
            spell_peak_notional = 0.0

    close_fy(cur_fy)  # settle the final (partial) financial year

    rows = []
    for fy in sorted(fy_winners.keys() | fy_all.keys()):
        rows.append({
            "fin_year": fy,
            "winners": fy_winners.get(fy, 0.0),
            "net_all": fy_all.get(fy, 0.0),
            "tax_paid": fy_tax_paid.get(fy, 0.0),
        })
    return E, rows, tds_total, spell_pnls, fy_all


def main():
    df = load()
    sig = trend_ensemble(df, C.THRESHOLD)
    res = run_backtest(df, sig, C.FEE, C.SLIPPAGE, C.BARS_PER_YEAR)
    ret, pos = res.returns, res.position

    cap = C.INITIAL_CAPITAL

    # --- pre-tax (should match locked ~2326%) ---
    pre_eq = cap * (1 + M.total_return(ret))
    pre_ret = M.total_return(ret) * 100

    # --- buy & hold, pre & after tax (one sell at the end) ---
    bh = df["close"].pct_change().fillna(0.0)
    bh_eq = cap * (1 + M.total_return(bh))
    bh_gain = bh_eq - cap
    bh_tax = EFFECTIVE * bh_gain if bh_gain > 0 else 0.0
    bh_tds = TDS_RATE * bh_eq          # 1% on the single final sale (creditable)
    bh_after = bh_eq - bh_tax

    # --- active, after tax (no offset) ---
    at_eq, fy_rows, tds_total, spell_pnls, fy_all = simulate_after_tax(ret, pos, EFFECTIVE)

    # --- active, after tax IF losses could offset (illustrative only) ---
    offset_eq, _, _, _, _ = simulate_after_tax_offset(ret, pos, EFFECTIVE)

    n_years = (df.index[-1] - df.index[0]).days / 365.25
    def cagr(mult):  # multiple over initial capital -> CAGR
        return (mult ** (1 / n_years) - 1) * 100 if mult > 0 else float("nan")

    wins = [p for p in spell_pnls if p > 0]
    losses = [p for p in spell_pnls if p <= 0]

    line = "=" * 74
    print("\n" + line)
    print("INDIAN AFTER-TAX REALITY CHECK  —  trend_ensemble (LOCKED)")
    print(line)
    print(f"  Sample        : {df.index[0].date()} -> {df.index[-1].date()}  ({n_years:.1f} yrs)")
    print(f"  Tax model     : {TAX_RATE*100:.0f}% + {CESS*100:.0f}% cess = {EFFECTIVE*100:.1f}% on VDA gains,")
    print(f"                  NO loss set-off, {TDS_RATE*100:.0f}% TDS per sell (creditable),")
    print(f"                  financial year Apr->Mar, tax paid at year end.")
    print(f"  Capital       : Rs {cap:,.0f}  (currency-agnostic; rates apply the same)")

    print("\n" + "-" * 74)
    print("TERMINAL WEALTH  (Rs, from Rs {:,.0f})".format(cap))
    print("-" * 74)
    print(f"  Active  PRE-tax        : Rs {pre_eq:,.0f}   ({pre_ret:,.0f}% | CAGR {cagr(pre_eq/cap):.1f}%)")
    print(f"  Active  AFTER-tax      : Rs {at_eq:,.0f}   "
          f"({(at_eq/cap-1)*100:,.0f}% | CAGR {cagr(at_eq/cap):.1f}%)   <-- reality")
    print(f"     (if losses COULD offset: Rs {offset_eq:,.0f}  ->  the no-offset rule alone "
          f"costs Rs {offset_eq-at_eq:,.0f})")
    print(f"  Buy & hold PRE-tax     : Rs {bh_eq:,.0f}   ({(bh_eq/cap-1)*100:,.0f}% | CAGR {cagr(bh_eq/cap):.1f}%)")
    print(f"  Buy & hold AFTER-tax   : Rs {bh_after:,.0f}   "
          f"({(bh_after/cap-1)*100:,.0f}% | CAGR {cagr(bh_after/cap):.1f}%)   <-- the thing to beat")

    print("\n" + "-" * 74)
    print("VERDICT")
    print("-" * 74)
    edge = at_eq - bh_after
    if edge > 0:
        print(f"  Active BEATS buy & hold after tax by Rs {edge:,.0f} "
              f"({(at_eq/bh_after-1)*100:+.0f}%).")
    else:
        print(f"  Active LOSES to buy & hold after tax by Rs {-edge:,.0f} "
              f"({(at_eq/bh_after-1)*100:+.0f}%).")
    print(f"  Tax dragged the active edge from PRE-tax {pre_ret:,.0f}% "
          f"down to {(at_eq/cap-1)*100:,.0f}%.")
    print(f"  Total 1% TDS cycled through sells: Rs {tds_total:,.0f} "
          f"(creditable — recoverable when you file, not a permanent loss).")

    print("\n" + "-" * 74)
    print(f"SPELLS: {len(spell_pnls)} total | {len(wins)} winners, {len(losses)} losers "
          f"({len(wins)/len(spell_pnls)*100:.0f}% win)")
    print(f"  winning spells booked Rs {sum(wins):,.0f} of gains  (all taxed at {EFFECTIVE*100:.1f}%)")
    print(f"  losing  spells lost   Rs {-sum(losses):,.0f}          (NOT deductible)")
    print("-" * 74)

    print("\nTAX BY FINANCIAL YEAR (winners taxed; losers give nothing):")
    hdr = f"  {'fin_year':<10}{'winners(Rs)':>14}{'net_all(Rs)':>14}{'tax_paid(Rs)':>14}"
    print(hdr)
    for r in fy_rows:
        print(f"  {r['fin_year']:<10}{r['winners']:>14,.0f}{r['net_all']:>14,.0f}{r['tax_paid']:>14,.0f}")
    tot_tax = sum(r["tax_paid"] for r in fy_rows)
    print(f"  {'TOTAL':<10}{'':>14}{'':>14}{tot_tax:>14,.0f}")
    print()


def simulate_after_tax_offset(ret: pd.Series, pos: pd.Series, effective_rate: float):
    """Same walk, but tax each FY's NET (winners+losers, floored at 0) — the
    hypothetical 'if set-off were allowed' case. Used only to isolate the cost
    of the no-offset rule."""
    idx = ret.index
    E = C.INITIAL_CAPITAL
    in_mkt = pos.values > 1e-9
    fy_net: dict[str, float] = {}
    cur_fy = fy_of(idx[0])
    spell_gain = 0.0
    tds = 0.0

    def close_fy(fy):
        nonlocal E
        net = fy_net.get(fy, 0.0)
        E -= effective_rate * net if net > 0 else 0.0

    for t in range(len(idx)):
        this_fy = fy_of(idx[t])
        if this_fy != cur_fy:
            close_fy(cur_fy)
            cur_fy = this_fy
        pnl = E * float(ret.iloc[t])
        E += pnl
        if in_mkt[t]:
            spell_gain += pnl
        if in_mkt[t] and (t + 1 >= len(idx) or not in_mkt[t + 1]):
            fy = fy_of(idx[t])
            fy_net[fy] = fy_net.get(fy, 0.0) + spell_gain
            spell_gain = 0.0
    close_fy(cur_fy)
    return E, None, tds, None, fy_net


if __name__ == "__main__":
    main()
