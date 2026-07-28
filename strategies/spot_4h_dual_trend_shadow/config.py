"""Frozen configuration for the paper-only four-hour spot shadow challenger."""

SLUG = "spot_4h_dual_trend_shadow"
VARIANT = "shadow_30_144_120_240"
STATUS = "FROZEN_PAPER_ONLY_SHADOW"
FROZEN_DATE = "2026-07-28"

SYMBOL = "BTCUSDT"
MARKET = "spot"
TIMEFRAME = "4h"
TIMEZONE = "UTC"
EXECUTION_DELAY_BARS = 1

EMA_FAST = 30
EMA_SLOW = 144
MOMENTUM_LOOKBACK = 120
TREND_SMA = 240
LONG_EXPOSURE = 1.0
FLAT_EXPOSURE = 0.0

# The research history is committed as an immutable signal seed so the live
# EMA recursion starts from the same 2017 anchor as the audited backtest.
PINNED_HISTORY = "data/BTCUSDT_4h_2017-08-17_2026-07-27.parquet"
PINNED_HISTORY_SHA256 = (
    "3247128a6ecc9d8e0c34520255ead8a0acc846e7b7495c4dceaf1b2bcc605aec"
)
BINANCE_PAGE_BARS = 1_000
MIN_WARMUP_BARS = max(EMA_SLOW, MOMENTUM_LOOKBACK, TREND_SMA)

FEE = 0.001
SLIPPAGE = 0.0005
MIN_TRADE_FRAC = 0.01
INITIAL_CAPITAL = 10_000.0
BARS_PER_YEAR = 6 * 365
MAX_GAP_BARS = 1

PAPER_ONLY = True
LIVE_TRADING_APPROVED = False
MIN_PROSPECTIVE_MONTHS = 12
