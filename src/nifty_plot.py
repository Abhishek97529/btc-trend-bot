"""Equity + drawdown chart for the Nifty strategy backtest.

Reads nifty_equity.csv (written by nifty_strategy.py) and renders a two-panel
figure: log-scale growth of 1 rupee, and drawdown. Palette = validated slots
1/2/3 (blue/orange/aqua) of the dataviz default categorical theme.
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports" / "legacy-markets" / "india"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e4e3df"
COL = {"BH": "#2a78d6", "REG100": "#eb6834", "EQRISK": "#1baf7a"}
LABEL = {"BH": "Buy & Hold", "REG100": "Regime (100-SMA)", "EQRISK": "Equal-Risk Levered"}

eq = pd.read_csv(REPORT_DIR / "nifty_equity.csv", parse_dates=["date"]).set_index("date")
series = ["BH", "REG100", "EQRISK"]

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 8), height_ratios=[2, 1],
                               sharex=True, facecolor=SURFACE)
for ax in (ax1, ax2):
    ax.set_facecolor(SURFACE)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=INK2)
    ax.grid(True, color=GRID, linewidth=0.7)

# --- panel 1: log equity ---
for s in series:
    ax1.plot(eq.index, eq[s], color=COL[s], linewidth=2.0, label=LABEL[s])
    y = eq[s].iloc[-1]
    ax1.annotate(f"{LABEL[s]}  {y:.1f}x", (eq.index[-1], y), color=COL[s],
                 fontsize=9, fontweight="bold", va="center", xytext=(6, 0),
                 textcoords="offset points")
ax1.set_yscale("log")
ax1.set_ylabel("Growth of ₹1 (log)", color=INK2, fontsize=10)
ax1.set_title("Nifty 50 — Strategy vs Buy & Hold (2007–2026, daily, costs incl.)",
              color=INK, fontsize=13, fontweight="bold", loc="left")

# --- panel 2: drawdown ---
for s in series:
    dd = (eq[s] / eq[s].cummax() - 1) * 100
    ax2.plot(dd.index, dd, color=COL[s], linewidth=1.5)
    ax2.fill_between(dd.index, dd, 0, color=COL[s], alpha=0.10)
ax2.set_ylabel("Drawdown (%)", color=INK2, fontsize=10)
ax2.set_xlabel("")
ax2.axhline(0, color=GRID, linewidth=1)

fig.subplots_adjust(right=0.80, hspace=0.12, left=0.08, top=0.93, bottom=0.06)
out = REPORT_DIR / "nifty_backtest.png"
fig.savefig(out, dpi=140, facecolor=SURFACE)
print(f"saved {out}")
