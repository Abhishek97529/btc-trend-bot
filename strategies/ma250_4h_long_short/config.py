"""Frozen configuration for MA250 +2x long / -0.5x short."""

SLUG = "fixed_2x_long_short_05_4h"
VARIANT = "long_short"
SYMBOL = "BTCUSDT"
MARKET = "perpetual"
TIMEFRAME = "4h"
TIMEZONE = "UTC"
MA_BARS = 250
LONG_EXPOSURE = 2.0
SHORT_EXPOSURE = 0.5
# Bybit VIP 0 perpetual TAKER rate; these runners cross the spread.
FEE = 0.00055
# Measured book sweep at $10-50k notional was <0.05 bps; held as a buffer.
SLIPPAGE = 0.0003
MAINTENANCE_MARGIN = 0.005
# Resize the position once realised exposure drifts >10% away from target.
REBALANCE_BAND = 0.10
INITIAL_CAPITAL = 10_000.0
MAX_GAP_BARS = 1
LIVE_TRADING_APPROVED = False
