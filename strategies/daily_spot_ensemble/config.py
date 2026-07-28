"""Frozen configuration for the daily BTCUSDT spot trend ensemble."""

SYMBOL = "BTCUSDT"
TIMEFRAME = "1d"
MARKET = "spot"
STRATEGY = "trend_ensemble"
TIMEZONE = "UTC"

THRESHOLD = 0.5
WARMUP_START = "2017-08-01"
WARMUP_DAYS = 260
SMA_WINDOWS = (50, 100, 200)
EMA_PAIRS = ((20, 50), (50, 100))
DONCHIAN_WINDOW = 55
MOMENTUM_LOOKBACK = 90

FEE = 0.001
SLIPPAGE = 0.0005
MIN_TRADE_FRAC = 0.01
INITIAL_CAPITAL = 10_000.0
BARS_PER_YEAR = 365
BACKTEST_START = "2018-06-01"

# The audit deliberately disables real orders until transactional exchange
# reconciliation and a fresh prospective approval are completed.
LIVE_TRADING_APPROVED = False

LOCKED_RESULTS_CORRECTED = {
    "net_profit_pct": 2047.58,
    "cagr_pct": 45.69,
    "sharpe": 1.136,
    "max_drawdown_pct": -38.27,
    "material_orders": 266,
}
