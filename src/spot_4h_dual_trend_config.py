"""Compatibility import for the organized four-hour dual-trend package."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies.spot_4h_dual_trend.config import *  # noqa: F401,F403

EXECUTION_DELAY_BARS = 1
