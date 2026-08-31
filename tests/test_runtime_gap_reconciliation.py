from __future__ import annotations

import unittest

import pandas as pd

from bot.runtime import reconcile_missed_holds


class GapReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.start = pd.Timestamp("2026-08-26 16:00:00+00:00")
        self.index = pd.date_range(
            self.start + pd.Timedelta(hours=4), periods=4, freq="4h"
        )

    def test_fast_forwards_only_historical_hold_bars(self):
        targets = pd.Series([1, 1, 1, 0], index=self.index)
        count, through = reconcile_missed_holds(
            str(self.start), self.index[-1], targets, 1, "test strategy"
        )
        self.assertEqual(count, 3)
        self.assertEqual(through, self.index[-2])

    def test_current_bar_transition_is_left_for_live_execution(self):
        targets = pd.Series([0], index=self.index[:1])
        count, through = reconcile_missed_holds(
            str(self.start), self.index[0], targets, 1, "test strategy"
        )
        self.assertEqual(count, 0)
        self.assertIsNone(through)

    def test_rejects_a_missed_transition(self):
        targets = pd.Series([1, 0, 1, 1], index=self.index)
        with self.assertRaisesRegex(
            RuntimeError, "missed test strategy trade at 2026-08-27 00:00:00"
        ):
            reconcile_missed_holds(
                str(self.start), self.index[-1], targets, 1, "test strategy"
            )

    def test_rejects_missing_signal_values(self):
        targets = pd.Series([1, float("nan"), 1], index=self.index[:3])
        with self.assertRaisesRegex(RuntimeError, "missing test strategy signal"):
            reconcile_missed_holds(
                str(self.start), self.index[2], targets, 1, "test strategy"
            )


if __name__ == "__main__":
    unittest.main()
