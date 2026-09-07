"""Long-history replay: bar aggregation, synthesis fidelity, P(pass) preservation."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from propfirm.data.bars import ticks_to_bars
from propfirm.data.replay import M15_SECONDS, m15_replay_path, replay_from_bars
from propfirm.data.synth_validate import compare_paths, ohlc_error, tick_stats
from propfirm.research.evaluate import compare_sources
from propfirm.research.montecarlo import gbm_path
from propfirm.strategy.controls import RandomEntryFactory


# --- bar aggregation ---------------------------------------------------------

def test_ticks_to_bars_computes_ohlc():
    # Two 10s bars, 1 tick/s. Bar 0: 100..109, bar 1: dips then rises.
    prices = list(range(100, 110)) + [108, 104, 106, 111, 105, 107, 109, 103, 102, 108]
    ticks = pd.DataFrame({"epoch": np.arange(20), "price": [float(p) for p in prices]})
    bars = ticks_to_bars(ticks, 10)
    assert list(bars["epoch"]) == [0, 10]
    assert bars.loc[0, "open"] == 100 and bars.loc[0, "close"] == 109
    assert bars.loc[0, "high"] == 109 and bars.loc[0, "low"] == 100
    assert bars.loc[1, "open"] == 108 and bars.loc[1, "close"] == 108
    assert bars.loc[1, "high"] == 111 and bars.loc[1, "low"] == 102


def test_ticks_to_bars_requires_columns():
    with pytest.raises(ValueError):
        ticks_to_bars(pd.DataFrame({"epoch": [0], "x": [1.0]}), 10)


# --- synthesis fidelity ------------------------------------------------------

def test_replay_reproduces_ohlc_anchors_closely():
    truth = gbm_path(2.0, seed=3, tick_seconds=2)
    bars = ticks_to_bars(truth[["epoch", "price"]], M15_SECONDS)
    synth = replay_from_bars(bars, tick_seconds=2, seed=3)
    err = ohlc_error(bars, synth, M15_SECONDS)
    assert err["open"] == pytest.approx(0.0)     # open/close are pinned exactly
    assert err["close"] == pytest.approx(0.0)
    # High/low are snapped, so small but bounded relative to the ~50k price level.
    assert err["high"] < 5.0 and err["low"] < 5.0


def test_replay_has_no_manufactured_momentum():
    # The check that killed earlier naive methods: lag-1 autocorrelation near zero.
    truth = gbm_path(3.0, seed=5, tick_seconds=2)
    bars = ticks_to_bars(truth[["epoch", "price"]], M15_SECONDS)
    synth = replay_from_bars(bars, tick_seconds=2, seed=5)
    stats = tick_stats(synth["price"].to_numpy(), 2)
    assert abs(stats["autocorr1"]) < 0.05
    assert abs(stats["uptick_frac"] - 0.5) < 0.03


def test_compare_paths_verdict_structure():
    truth = gbm_path(2.0, seed=7, tick_seconds=2)
    bars = ticks_to_bars(truth[["epoch", "price"]], M15_SECONDS)
    synth = replay_from_bars(bars, tick_seconds=2, seed=7)
    rep = compare_paths(synth["price"].to_numpy(), 2, reference=truth["price"].to_numpy())
    # sigma / autocorr / balance should hold; kurtosis may be flagged (H/L snapping).
    assert rep["checks"]["sigma"] and rep["checks"]["autocorr1"]
    assert rep["checks"]["uptick_frac"]


# --- the decisive test: P(pass) preserved through the M15 round-trip ---------

def test_m15_replay_path_yields_a_usable_series():
    ticks = m15_replay_path(2.0, seed=1, tick_seconds=10)
    assert set(ticks.columns) == {"epoch", "price"}
    assert len(ticks) > 1000
    assert ticks["epoch"].is_monotonic_increasing


def test_p_pass_survives_the_m15_round_trip():
    # Random entry on the true path vs on its M15 replay: no significant difference.
    dec = compare_sources(
        RandomEntryFactory(risk_pct=1.0, rr=2.0, trades_per_day=3.0),
        gbm_path, m15_replay_path, seeds=range(0, 24),
        days=15.0, tick_seconds=10, label_a="truth", label_b="m15")
    assert dec["p_value"] > 0.05
