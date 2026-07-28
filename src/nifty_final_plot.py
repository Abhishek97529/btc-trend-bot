"""Chart: Dip-Leverage strategy (0.5x & 1.0x overlay) vs buy-and-hold."""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports" / "legacy-markets" / "india"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nifty_final import load_close, net_returns, strategy_pos

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
COL = {"BH": "#2a78d6", "D05": "#1baf7a", "D10": "#eb6834"}
LAB = {"BH": "Buy & Hold", "D05": "Dip-Lev 0.5x (balanced)", "D10": "Dip-Lev 1.0x (aggressive)"}

close = load_close()
ret = close.pct_change().fillna(0.0)
eq = pd.DataFrame({
    "BH": (1 + net_returns(pd.Series(1.0, index=close.index), ret)).cumprod(),
    "D05": (1 + net_returns(strategy_pos(close, overlay=0.5), ret)).cumprod(),
    "D10": (1 + net_returns(strategy_pos(close, overlay=1.0), ret)).cumprod(),
}, index=close.index)

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 8), height_ratios=[2, 1],
                               sharex=True, facecolor=SURFACE)
for ax in (ax1, ax2):
    ax.set_facecolor(SURFACE)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=INK2)
    ax.grid(True, color=GRID, linewidth=0.7)

for k in ["BH", "D05", "D10"]:
    ax1.plot(eq.index, eq[k], color=COL[k], linewidth=2.0, label=LAB[k])
    y = eq[k].iloc[-1]
    ax1.annotate(f"{y:.1f}x", (eq.index[-1], y), color=COL[k], fontsize=9,
                 fontweight="bold", va="center", xytext=(6, 0), textcoords="offset points")
ax1.set_yscale("log")
ax1.set_ylabel("Growth of ₹1 (log)", color=INK2, fontsize=10)
ax1.set_title("Nifty 50 — Dip-Leverage strategy vs Buy & Hold (2007–2026, costs incl.)",
              color=INK, fontsize=13, fontweight="bold", loc="left")
ax1.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK)

for k in ["BH", "D05", "D10"]:
    dd = (eq[k] / eq[k].cummax() - 1) * 100
    ax2.plot(dd.index, dd, color=COL[k], linewidth=1.4)
ax2.fill_between(eq.index, (eq["BH"] / eq["BH"].cummax() - 1) * 100, 0,
                 color=COL["BH"], alpha=0.08)
ax2.set_ylabel("Drawdown (%)", color=INK2, fontsize=10)
ax2.axhline(0, color=GRID, linewidth=1)

fig.subplots_adjust(right=0.94, hspace=0.12, left=0.08, top=0.93, bottom=0.06)
out = REPORT_DIR / "nifty_final.png"
fig.savefig(out, dpi=140, facecolor=SURFACE)
print(f"saved {out}")
