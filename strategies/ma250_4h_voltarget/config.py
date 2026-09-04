"""Frozen configuration for the MA250 volatility-targeted challenger.

Paper-only. Same SMA(250) entry rule as the two incumbent MA250 accounts, but
sized by target_vol / realised_vol and capped at 1.5x instead of holding a
constant 2.0x.

Backtest rationale (docs/audits/STRATEGY_IMPROVEMENTS.md): constant leverage
scales returns linearly, so 1x, 1.5x and 2x all produced the same 1.12 Sharpe
while the probability of a worse-than-70% drawdown rose from 14% to 96%.
Volatility targeting raised Sharpe to 1.20 and Sortino to 1.19, cut the tail
probability to 6.7%, reduced drawdown in every regime tested, and was the only
configuration to survive a synthetic -50% shock.
"""

SLUG = "ma250_voltarget_4h"
VARIANT = "voltarget"
STATUS = "FROZEN_PAPER_ONLY_CHALLENGER"
FROZEN_DATE = "2026-09-04"

SYMBOL = "BTCUSDT"
MARKET = "perpetual"
TIMEFRAME = "4h"
TIMEZONE = "UTC"
MA_BARS = 250

# Direction rule is identical to the incumbents; only the sizing differs.
LONG_EXPOSURE = 1.0
SHORT_EXPOSURE = 0.0

# Annualised realised-volatility target and the hard leverage ceiling. The cap,
# not the average, is what decides liquidation survival under a shock.
VOL_TARGET = 0.50
VOL_LOOKBACK = 30
MAX_LEVERAGE = 1.5
BARS_PER_YEAR = 6 * 365

# Resize only on a material change, which keeps vol-targeting turnover close to
# the fixed-size rule rather than rebalancing every bar.
REBALANCE_BAND = 0.20

# Bybit VIP 0 perpetual TAKER rate; these runners cross the spread.
FEE = 0.00055
# Measured book sweep at $10-50k notional was <0.05 bps; held as a buffer.
SLIPPAGE = 0.0003
MAINTENANCE_MARGIN = 0.005
INITIAL_CAPITAL = 10_000.0
MAX_GAP_BARS = 1

PAPER_ONLY = True
LIVE_TRADING_APPROVED = False
MIN_PROSPECTIVE_MONTHS = 12
