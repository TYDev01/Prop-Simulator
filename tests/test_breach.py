"""Breach detection at exact boundaries and the pass-gating rules (§5.1, §6)."""
from __future__ import annotations

import pytest

from propfirm.rules.breach import (
    ChallengeState,
    Outcome,
    check,
    daily_loss_floor,
    max_loss_floor,
)
from propfirm.rules.ruleset import STRICT_100K
from propfirm.sim.ledger import Ledger


def _state() -> ChallengeState:
    return ChallengeState(rules=STRICT_100K, phase_index=0,
                          start_epoch=0, start_balance=100_000.0)


def _ledger() -> Ledger:
    return Ledger(spec=None, starting_balance=100_000.0)  # spec unused by these paths


# --- floors ------------------------------------------------------------------

def test_floor_values(vol75):
    st, led = _state(), _ledger()
    led.day_start_equity = 100_000.0
    led.equity_hwm = 100_000.0
    assert daily_loss_floor(st, led) == pytest.approx(95_000.0)   # 5% of day start
    assert max_loss_floor(st, led) == pytest.approx(90_000.0)     # 10% off equity hwm


# --- daily loss at the exact boundary ---------------------------------------

def test_daily_breach_is_inclusive_at_the_floor():
    st, led = _state(), _ledger()
    led.day_start_equity = 100_000.0
    led.equity_hwm = 100_000.0
    assert check(st, led, mark=0.0, epoch=1, equity=95_000.0) is Outcome.BREACHED_DAILY


def test_one_cent_above_daily_floor_survives():
    st, led = _state(), _ledger()
    led.day_start_equity = 100_000.0
    led.equity_hwm = 100_000.0
    assert check(st, led, mark=0.0, epoch=1, equity=95_000.01) is Outcome.RUNNING


# --- max total loss, equity-trailing ----------------------------------------

def test_total_breach_at_trailing_floor():
    st, led = _state(), _ledger()
    led.day_start_equity = 90_000.0     # keep the daily floor (85_500) out of the way
    led.equity_hwm = 100_000.0
    assert check(st, led, mark=0.0, epoch=1, equity=90_000.0) is Outcome.BREACHED_TOTAL


# --- expiry ------------------------------------------------------------------

def test_expiry_past_the_day_limit():
    st, led = _state(), _ledger()
    led.day_start_equity = 100_000.0
    led.equity_hwm = 100_000.0
    past = int(30 * 86400) + 100        # phase1 max_days = 30
    assert check(st, led, mark=0.0, epoch=past, equity=100_000.0) is Outcome.EXPIRED


# --- pass gating -------------------------------------------------------------

def _at_target(led: Ledger) -> None:
    led.day_start_equity = 100_000.0
    led.equity_hwm = 108_000.0


def test_target_blocked_by_min_trading_days():
    st, led = _state(), _ledger()
    _at_target(led)
    out = check(st, led, mark=0.0, epoch=1, equity=108_000.0)
    assert out is Outcome.RUNNING
    assert any("trading day" in b for b in st.blocked_by)


def test_target_blocked_by_consistency_cap():
    st, led = _state(), _ledger()
    _at_target(led)
    led.trading_days = {"d1", "d2", "d3", "d4"}
    led.daily_pnl = {"d1": 5_000.0, "d2": 1_000.0, "d3": 1_000.0, "d4": 1_000.0}
    out = check(st, led, mark=0.0, epoch=1, equity=108_000.0)
    assert out is Outcome.RUNNING
    assert any("%" in b for b in st.blocked_by)


def test_target_passes_when_all_gates_clear():
    st, led = _state(), _ledger()
    _at_target(led)
    led.trading_days = {"d1", "d2", "d3", "d4"}
    led.daily_pnl = {"d1": 2_000.0, "d2": 2_000.0, "d3": 2_000.0, "d4": 2_000.0}
    assert check(st, led, mark=0.0, epoch=1, equity=108_000.0) is Outcome.PASSED


def test_open_position_defers_the_pass():
    from propfirm.sim.ledger import Position
    st, led = _state(), _ledger()
    _at_target(led)
    led.trading_days = {"d1", "d2", "d3", "d4"}
    led.daily_pnl = {"d1": 2_000.0, "d2": 2_000.0, "d3": 2_000.0, "d4": 2_000.0}
    led.positions.append(Position(direction=+1, lots=1.0, entry_price=1.0, opened_epoch=0))
    out = check(st, led, mark=0.0, epoch=1, equity=108_000.0)
    assert out is Outcome.RUNNING
    assert any("position" in b for b in st.blocked_by)
