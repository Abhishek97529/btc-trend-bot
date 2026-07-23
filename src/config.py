"""
LOCKED configuration — single source of truth for the BTC spot strategy.

Frozen 2026-07-23. Do NOT tune these per-run. Every component (backtest, paper
bot, Pine port, spec) must reference these exact values. Changing anything here
means re-validating the whole strategy from scratch.
"""
from __future__ import annotations

# ---- Instrument ------------------------------------------------------------ #
SYMBOL = "BTCUSDT"          # Binance SPOT (not the .P perpetual)
TIMEFRAME = "1d"            # daily candles, UTC
MARKET = "spot"             # long/flat only — NO leverage, NO shorting

# ---- Strategy -------------------------------------------------------------- #
STRATEGY = "trend_ensemble"
THRESHOLD = 0.5             # agreement gate; robust across 0.3–0.8
WARMUP_DAYS = 260           # > 200 for the SMA(200)

# The 7 trend votes (fixed lengths — frozen):
#   1 close>SMA50  2 close>SMA100  3 close>SMA200
#   4 EMA20>EMA50  5 EMA50>EMA100  6 Donchian(55) breakout state  7 ROC(90)>0
SMA_WINDOWS = (50, 100, 200)
EMA_PAIRS = ((20, 50), (50, 100))
DONCHIAN_WINDOW = 55
MOMENTUM_LOOKBACK = 90

# ---- Costs (charged on turnover) ------------------------------------------ #
FEE = 0.001                 # 0.10% Binance spot taker, per side
SLIPPAGE = 0.0005           # 5 bps, per side
MIN_TRADE_FRAC = 0.01       # skip rebalances < 1% of equity (dust filter)

# ---- Accounting ------------------------------------------------------------ #
INITIAL_CAPITAL = 10_000.0
BARS_PER_YEAR = 365
BACKTEST_START = "2018-06-01"

# ---- Locked backtest results (2018-06-01 → 2026-07-23, net of costs) ------- #
# Snapshot recorded at lock time; a correct reimplementation should match these.
LOCKED_RESULTS = {
    "net_profit_pct": 2326.37,
    "buy_hold_pct": 762.23,
    "cagr_pct": 47.88,
    "sharpe": 1.18,
    "sortino": 1.27,
    "max_drawdown_pct": -38.26,
    "profit_factor": 3.67,
    "total_closed_trades": 27,
    "percent_profitable_pct": 25.93,
    "time_in_market_pct": 54.0,
}
