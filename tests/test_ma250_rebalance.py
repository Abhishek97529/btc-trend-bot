"""Exposure-drift rebalancing for the MA250 perpetual paper runners."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT)]
os.environ.setdefault("FIXED_4H_VARIANT", "long_flat")

bot = pytest.importorskip("bot.paper_4h_bot")


def actual_exposure(qty: float, equity: float, price: float) -> float:
    return qty * price / equity


def test_holds_inside_the_tolerance_band():
    # 2x target held through a small favourable move stays inside the band.
    price, equity = 66_000.0, 10_600.0
    qty = 2.0 * 10_000.0 / 64_000.0
    assert abs(actual_exposure(qty, equity, price) - 2.0) / 2.0 < 0.10
    assert not bot.exposure_drifted(2.0, equity, price, qty)


def test_rebalances_after_leverage_decays_on_a_winning_trend():
    # The live drift observed in the paper account: 2.0x target, 1.66x actual.
    price, equity = 81_174.3, 14_303.17
    qty = 0.29255341
    assert actual_exposure(qty, equity, price) == pytest.approx(1.66, abs=0.01)
    assert bot.exposure_drifted(2.0, equity, price, qty)


def test_rebalances_when_a_losing_trade_inflates_leverage():
    # Fixed quantity raises realised leverage as equity falls; that is the
    # dangerous direction and must also trigger a resize.
    qty = 2.0 * 10_000.0 / 64_000.0
    price, equity = 57_000.0, 7_500.0
    assert actual_exposure(qty, equity, price) > 2.2
    assert bot.exposure_drifted(2.0, equity, price, qty)


def test_short_target_uses_magnitude_of_exposure():
    qty = -0.5 * 10_000.0 / 64_000.0
    assert not bot.exposure_drifted(-0.5, 10_000.0, 64_000.0, qty)
    assert bot.exposure_drifted(-0.5, 7_000.0, 64_000.0, qty)


def test_flat_target_never_rebalances():
    assert not bot.exposure_drifted(0.0, 10_000.0, 64_000.0, 0.0)


def test_zero_band_disables_rebalancing(monkeypatch):
    monkeypatch.setattr(bot.C, "REBALANCE_BAND", 0.0, raising=False)
    assert not bot.exposure_drifted(2.0, 14_303.17, 81_174.3, 0.29255341)


def test_rebalanced_quantity_restores_the_target_exposure():
    price, equity = 81_174.3, 14_303.17
    qty = bot.cost_aware_target_qty(2.0, equity, price, 0.29255341)
    cost = abs(qty - 0.29255341) * price * (bot.C.FEE + bot.C.SLIPPAGE)
    assert actual_exposure(qty, equity - cost, price) == pytest.approx(2.0, abs=1e-6)


def _frame(daily_vol: float, bars: int = 400) -> "pd.DataFrame":
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(0)
    steps = rng.normal(0.0, daily_vol, bars)
    close = 60_000 * np.exp(np.cumsum(steps))
    index = pd.date_range("2024-01-01", periods=bars, freq="4h", tz="UTC")
    return pd.DataFrame({"close": close}, index=index)


def test_volatility_scaling_is_disabled_for_the_incumbent_configs():
    # The frozen accounts must keep constant sizing; only the challenger scales.
    assert getattr(bot.C, "VOL_TARGET", None) is None
    assert bot.volatility_scale(_frame(0.01)) is None


def test_volatility_scale_sizes_down_when_volatility_is_high(monkeypatch):
    monkeypatch.setattr(bot.C, "VOL_TARGET", 0.50, raising=False)
    monkeypatch.setattr(bot.C, "MAX_LEVERAGE", 1.5, raising=False)
    calm = bot.volatility_scale(_frame(0.005))
    wild = bot.volatility_scale(_frame(0.05))
    assert wild < calm
    assert wild < 1.0


def test_volatility_scale_respects_the_leverage_cap(monkeypatch):
    # The cap, not the average, is what decides shock survival.
    monkeypatch.setattr(bot.C, "VOL_TARGET", 0.50, raising=False)
    monkeypatch.setattr(bot.C, "MAX_LEVERAGE", 1.5, raising=False)
    assert bot.volatility_scale(_frame(0.0001)) == pytest.approx(1.5)


def test_volatility_scale_fails_closed_without_data(monkeypatch):
    import pandas as pd

    monkeypatch.setattr(bot.C, "VOL_TARGET", 0.50, raising=False)
    flat = pd.DataFrame(
        {"close": [60_000.0] * 40},
        index=pd.date_range("2024-01-01", periods=40, freq="4h", tz="UTC"),
    )
    with pytest.raises(RuntimeError, match="refusing to size blind"):
        bot.volatility_scale(flat)
