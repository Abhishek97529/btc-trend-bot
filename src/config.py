"""Compatibility import for the organized daily strategy package."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies.daily_spot_ensemble.config import *  # noqa: F401,F403

# Older research scripts expect this name. It now points to the corrected audit.
LOCKED_RESULTS = LOCKED_RESULTS_CORRECTED
