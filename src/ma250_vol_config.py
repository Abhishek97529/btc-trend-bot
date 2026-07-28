"""Frozen candidate configuration for prospective MA250 volatility-control paper test."""

SYMBOL = "BTCUSDT"
MARKET = "perpetual"
TIMEFRAME = "4h"

MA_BARS = 250
VOL_WINDOW_BARS = 60
TARGET_ANNUAL_VOL = 0.40
MIN_LEVERAGE = 0.25
MAX_LEVERAGE = 2.0
REBALANCE_BARS = 42

FEE = 0.0004
SLIPPAGE = 0.0003
MAINTENANCE_MARGIN = 0.005
BARS_PER_YEAR = 6 * 365

# This is a paper candidate, not a live-trading approval.
STATUS = "FROZEN_FOR_PROSPECTIVE_PAPER_TEST"
