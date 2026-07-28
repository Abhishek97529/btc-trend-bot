from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from bot import paper_shadow_4h_bot as runner
from strategies.spot_4h_dual_trend_shadow import config as C


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / C.PINNED_HISTORY
TARGET_SHA256 = (
    "210bd09ce3b332478825a75e6f4d9d69"
    "eba880960589e883c978bf12ad767d66"
)


class ShadowSignalRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = pd.read_parquet(FIXTURE).sort_index()
        cls.indicators = runner.signal_frame(cls.frame)
        cls.target = cls.indicators["target"].astype("int8")

    def test_frozen_contract_and_fixture(self):
        self.assertEqual(
            (
                C.EMA_FAST,
                C.EMA_SLOW,
                C.MOMENTUM_LOOKBACK,
                C.TREND_SMA,
                C.EXECUTION_DELAY_BARS,
                C.FEE + C.SLIPPAGE,
            ),
            (30, 144, 120, 240, 1, 0.0015),
        )
        self.assertTrue(C.PAPER_ONLY)
        self.assertFalse(C.LIVE_TRADING_APPROVED)
        self.assertEqual(
            hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            C.PINNED_HISTORY_SHA256,
        )
        self.assertEqual(len(self.frame), 19_580)
        self.assertEqual(
            self.frame.index[0],
            pd.Timestamp("2017-08-17 04:00:00+00:00"),
        )
        self.assertEqual(
            self.frame.index[-1],
            pd.Timestamp("2026-07-27 00:00:00+00:00"),
        )

    def test_golden_target_and_transition_counts(self):
        payload = pd.util.hash_pandas_object(
            self.target, index=True
        ).values.tobytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), TARGET_SHA256)
        score = self.target.loc[pd.Timestamp("2019-01-01", tz="UTC"):]
        changes = score.diff().fillna(score)
        self.assertEqual(int(score.sum()), 7_671)
        self.assertEqual(int((changes > 0).sum()), 177)
        self.assertEqual(int((changes < 0).sum()), 176)

    def test_golden_backtest_metrics(self):
        position = self.target.shift(C.EXECUTION_DELAY_BARS).fillna(0)
        open_return = self.frame["open"].shift(-1).div(
            self.frame["open"]
        ).sub(1)
        changes = position.diff().fillna(position)
        returns = position * open_return - changes.abs() * (C.FEE + C.SLIPPAGE)
        valid = open_return.notna()
        start = pd.Timestamp("2019-01-01", tz="UTC")
        returns = returns[valid].loc[start:]
        position = position[valid].loc[start:]
        changes = changes[valid].loc[start:]
        equity = (1 + returns).cumprod()
        years = (
            returns.index[-1] - returns.index[0]
        ).total_seconds() / (365.25 * 86_400)
        total = (equity.iloc[-1] - 1) * 100
        cagr = (equity.iloc[-1] ** (1 / years) - 1) * 100
        sharpe = (
            returns.mean() / returns.std() * np.sqrt(C.BARS_PER_YEAR)
        )
        drawdown = (equity / equity.cummax() - 1).min() * 100
        self.assertAlmostEqual(total, 4_606.5045867, places=5)
        self.assertAlmostEqual(cagr, 66.361493, places=5)
        self.assertAlmostEqual(sharpe, 1.4809497, places=6)
        self.assertAlmostEqual(drawdown, -34.8245845, places=5)
        self.assertEqual(int((changes.abs() > 0.5).sum()), 353)
        self.assertEqual(set(position.unique()), {0.0, 1.0})

    def test_prefix_causality_and_strict_ties(self):
        for stop in (241, 500, 2_000, len(self.frame)):
            prefix = runner.signal_frame(self.frame.iloc[:stop])["target"]
            self.assertEqual(prefix.iloc[-1], self.target.iloc[stop - 1])
        constant = self.frame.iloc[:300].copy()
        constant.loc[:, "close"] = 100.0
        self.assertEqual(int(runner.signal_frame(constant)["target"].sum()), 0)


class ShadowPaperLedgerTests(unittest.TestCase):
    def _runtime(self):
        runtime = ROOT / "tests" / "_shadow_runtime_fixture"
        runtime.mkdir(exist_ok=True)
        paths = [
            runtime / "state.json",
            runtime / "status.json",
            runtime / "trades.csv",
        ]
        for path in paths:
            if path.exists():
                path.unlink()
        self.addCleanup(
            lambda: [
                path.unlink()
                for path in paths
                if path.exists()
            ]
        )
        return runtime

    def _signal(self, bar, target):
        return {
            "bar_time": bar,
            "closed_price": 100.0,
            "ema_fast": 101.0,
            "ema_slow": 100.0,
            "momentum": 0.1,
            "sma": 99.0,
            "conditions": {
                "ema_trend": bool(target),
                "momentum_positive": bool(target),
                "sma_regime": bool(target),
            },
            "filters_passed": 3 if target else 0,
            "target": target,
        }

    def test_fee_aware_enter_duplicate_hold_and_exit(self):
        start = pd.Timestamp("2026-07-28 00:00:00+00:00")
        market = (
            pd.DataFrame(),
            100.0,
            99.0,
            25.0,
            "fixture",
            start + pd.Timedelta(minutes=25),
        )
        runtime = self._runtime()
        with (
            patch.object(runner, "STATE", runtime / "state.json"),
            patch.object(runner, "STATUS", runtime / "status.json"),
            patch.object(runner, "TRADES", runtime / "trades.csv"),
            patch.object(runner, "market_data", return_value=market),
            patch.object(runner, "signal", return_value=self._signal(start, 1)),
            patch.object(runner, "notify"),
        ):
            entered = runner.run()
            self.assertEqual(entered["action"], "ENTER")
            self.assertGreaterEqual(entered["cash_usd"], 0)
            state = json.loads((runtime / "state.json").read_text())
            self.assertGreater(state["btc"], 0)
            state_after_enter = (runtime / "state.json").read_bytes()
            self.assertIsNone(runner.run())
            self.assertEqual(
                (runtime / "state.json").read_bytes(), state_after_enter
            )

            next_bar = start + pd.Timedelta(hours=4)
            exit_market = (
                pd.DataFrame(),
                110.0,
                109.0,
                25.0,
                "fixture",
                next_bar + pd.Timedelta(minutes=25),
            )
            with (
                patch.object(runner, "market_data", return_value=exit_market),
                patch.object(
                    runner, "signal", return_value=self._signal(next_bar, 0)
                ),
            ):
                exited = runner.run()
            self.assertEqual(exited["action"], "EXIT")
            final_state = json.loads((runtime / "state.json").read_text())
            self.assertEqual(final_state["btc"], 0.0)
            self.assertGreater(final_state["cash"], 10_000.0)

    def test_dry_run_writes_nothing(self):
        bar = pd.Timestamp("2026-07-28 00:00:00+00:00")
        market = (
            pd.DataFrame(),
            100.0,
            99.0,
            25.0,
            "fixture",
            bar + pd.Timedelta(minutes=25),
        )
        runtime = self._runtime()
        with (
            patch.object(runner, "STATE", runtime / "state.json"),
            patch.object(runner, "STATUS", runtime / "status.json"),
            patch.object(runner, "TRADES", runtime / "trades.csv"),
            patch.object(runner, "market_data", return_value=market),
            patch.object(runner, "signal", return_value=self._signal(bar, 1)),
        ):
            runner.run(dry=True)
        self.assertEqual(list(runtime.iterdir()), [])

    def test_gap_fails_before_state_mutation(self):
        last_bar = pd.Timestamp("2026-07-28 00:00:00+00:00")
        next_bar = last_bar + pd.Timedelta(hours=8)
        market = (
            pd.DataFrame(),
            100.0,
            99.0,
            25.0,
            "fixture",
            next_bar + pd.Timedelta(minutes=25),
        )
        runtime = self._runtime()
        state_path = runtime / "state.json"
        state = runner.default_state()
        state["last_bar"] = str(last_bar)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        before = state_path.read_bytes()
        with (
            patch.object(runner, "STATE", state_path),
            patch.object(runner, "STATUS", runtime / "status.json"),
            patch.object(runner, "TRADES", runtime / "trades.csv"),
            patch.object(runner, "market_data", return_value=market),
            patch.object(
                runner, "signal", return_value=self._signal(next_bar, 1)
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "unreconciled gap"):
                runner.run()
        self.assertEqual(state_path.read_bytes(), before)
        self.assertFalse((runtime / "status.json").exists())
        self.assertFalse((runtime / "trades.csv").exists())


if __name__ == "__main__":
    unittest.main()
