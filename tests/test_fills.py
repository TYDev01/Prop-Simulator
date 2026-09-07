"""Fill engine: the gap-fill honesty rules (BUILD_PROMPT §5, REMAINING.md §5.1).

A stop is a trigger, not a guaranteed price: jumping through it fills at the worse
of the level and the price available. A target is a resting order: a favourable
jump does not pay better than the level. Getting either wrong manufactures profit.
"""
from __future__ import annotations

import pytest

from propfirm.sim.fills import FillEngine, quote_from_mid
from propfirm.sim.ledger import Position
from tests.conftest import spec_with


# A zero-spread, whole-number spec keeps the arithmetic legible.
SPEC = spec_with(digits=2, point=0.01, spread_points=0.0,
                 stops_level_points=0.0, min_lot=0.001)


def _pos(direction: int, sl=None, tp=None) -> Position:
    return Position(direction=direction, lots=1.0, entry_price=100.0,
                    opened_epoch=0, sl=sl, tp=tp)


def test_no_exit_when_level_untouched():
    fe = FillEngine(spec=SPEC)
    assert fe.check_exit(_pos(+1, sl=90.0, tp=120.0), prev_mid=101.0, mid=102.0) is None


def test_long_stop_gap_fills_at_the_gap_not_the_level():
    fe = FillEngine(spec=SPEC)
    # Price leaps from 110 down to 95, straight through a stop at 100.
    fill, reason = fe.check_exit(_pos(+1, sl=100.0), prev_mid=110.0, mid=95.0)
    assert reason == "sl"
    assert fill == pytest.approx(95.0)          # the worse available price, not 100


def test_long_stop_without_gap_fills_at_the_level():
    fe = FillEngine(spec=SPEC)
    # The step brackets the stop but the far end is above it: no adverse gap.
    fill, reason = fe.check_exit(_pos(+1, sl=100.0), prev_mid=99.5, mid=100.5)
    assert reason == "sl"
    assert fill == pytest.approx(100.0)


def test_short_stop_gap_fills_worse():
    fe = FillEngine(spec=SPEC)
    # Short stopped out as price gaps up through 100 to 108.
    fill, reason = fe.check_exit(_pos(-1, sl=100.0), prev_mid=92.0, mid=108.0)
    assert reason == "sl"
    assert fill == pytest.approx(108.0)         # worse (higher) than the 100 level


def test_target_does_not_pay_better_than_the_level():
    fe = FillEngine(spec=SPEC)
    # Favourable jump straight through a take-profit at 120 up to 130.
    fill, reason = fe.check_exit(_pos(+1, tp=120.0), prev_mid=118.0, mid=130.0)
    assert reason == "tp"
    assert fill == pytest.approx(120.0)         # the resting order, not 130


def test_stop_taken_first_when_a_step_brackets_both():
    fe = FillEngine(spec=SPEC)
    # One step spans both SL (100) and TP (120); the pessimistic reading wins.
    fill, reason = fe.check_exit(_pos(+1, sl=100.0, tp=120.0), prev_mid=98.0, mid=122.0)
    assert reason == "sl"
    assert fill == pytest.approx(100.0)


def test_quote_straddles_mid_by_half_the_spread(vol75):
    q = quote_from_mid(50_000.0, vol75)
    half = vol75.spread_points * vol75.point / 2.0
    assert q.bid == pytest.approx(50_000.0 - half)
    assert q.ask == pytest.approx(50_000.0 + half)
    assert q.mid == pytest.approx(50_000.0)
