"""Ledger arithmetic, day-rollover, and swap accrual against hand-worked cases."""
from __future__ import annotations

import pytest

from propfirm.sim.ledger import (
    BROKER_UTC_OFFSET_HOURS,
    Ledger,
    Position,
    broker_day,
    broker_day_bounds,
)
from tests.conftest import spec_with


# --- P&L arithmetic ----------------------------------------------------------

def test_pnl_sign_and_magnitude(vol75):
    # contract_size = 1.0, so a 10-point move on 1 lot is $10.
    assert vol75.pnl(+1, 1.0, 100.0, 110.0) == pytest.approx(10.0)
    assert vol75.pnl(-1, 1.0, 100.0, 110.0) == pytest.approx(-10.0)
    assert vol75.pnl(+1, 2.5, 100.0, 104.0) == pytest.approx(10.0)


def test_equity_is_balance_plus_floating(vol75):
    led = Ledger(spec=vol75, starting_balance=100_000.0)
    led.open(Position(direction=+1, lots=1.0, entry_price=50_000.0, opened_epoch=0))
    # Long 1 lot from 50000, mark 50010 -> +10 floating.
    assert led.floating(50_010.0) == pytest.approx(10.0)
    assert led.equity(50_010.0) == pytest.approx(100_010.0)


def test_close_moves_floating_to_realised(vol75):
    led = Ledger(spec=vol75, starting_balance=100_000.0)
    pos = Position(direction=+1, lots=1.0, entry_price=50_000.0, opened_epoch=0)
    led.open(pos)
    led.close(pos, exit_price=50_020.0, epoch=100, reason="tp")
    assert led.balance == pytest.approx(100_020.0)
    assert led.positions == []
    assert led.daily_pnl[broker_day(100)] == pytest.approx(20.0)


# --- day boundary ------------------------------------------------------------

def test_broker_day_bounds_contain_epoch_and_span_one_day():
    epoch = 1_780_123_456
    start, end = broker_day_bounds(epoch, BROKER_UTC_OFFSET_HOURS)
    assert start <= epoch < end
    assert end - start == 86_400
    # The integer fast path must name the same day as the string formatter.
    assert broker_day(start) == broker_day(epoch)
    assert broker_day(end) != broker_day(epoch)


def test_day_start_equity_resets_at_rollover(vol75):
    led = Ledger(spec=vol75, starting_balance=100_000.0)
    epoch0 = 1_780_000_000
    _, end = broker_day_bounds(epoch0)

    led.mark(epoch0, 50_000.0)
    assert led.current_day == broker_day(epoch0)
    day1_start = led.day_start_equity

    # Realise a gain, then cross into the next broker day.
    led.balance += 500.0
    led.mark(end, 50_000.0)
    assert led.current_day == broker_day(end)
    assert led.day_start_equity == pytest.approx(day1_start + 500.0)


def test_equity_hwm_ratchets_up_only(vol75):
    led = Ledger(spec=vol75, starting_balance=100_000.0)
    led.mark(1_780_000_000, 50_000.0)
    led.balance = 101_000.0
    led.mark(1_780_000_002, 50_000.0)
    assert led.equity_hwm == pytest.approx(101_000.0)
    led.balance = 100_000.0
    led.mark(1_780_000_004, 50_000.0)
    assert led.equity_hwm == pytest.approx(101_000.0)  # does not fall back


# --- swap accrual (REMAINING.md §1.3) ---------------------------------------

def test_swap_charged_once_per_rollover_not_on_first_day():
    spec = spec_with(swap_long=-5.0, swap_short=-3.0)
    led = Ledger(spec=spec, starting_balance=100_000.0)
    led.open(Position(direction=+1, lots=2.0, entry_price=100.0, opened_epoch=0))

    epoch0 = 1_780_000_000
    _, end1 = broker_day_bounds(epoch0)
    _, end2 = broker_day_bounds(end1)

    led.mark(epoch0, 100.0)
    assert led.swap_paid == 0.0                 # nothing held overnight yet
    assert led.balance == pytest.approx(100_000.0)

    led.mark(end1, 100.0)                        # cross one rollover: -5 * 2 lots
    assert led.swap_paid == pytest.approx(-10.0)
    assert led.balance == pytest.approx(99_990.0)

    led.mark(end2, 100.0)                        # cross a second rollover
    assert led.swap_paid == pytest.approx(-20.0)
    assert led.balance == pytest.approx(99_980.0)


def test_no_swap_when_flat():
    spec = spec_with(swap_long=-5.0, swap_short=-3.0)
    led = Ledger(spec=spec, starting_balance=100_000.0)
    epoch0 = 1_780_000_000
    _, end1 = broker_day_bounds(epoch0)
    led.mark(epoch0, 100.0)
    led.mark(end1, 100.0)
    assert led.swap_paid == 0.0
    assert led.balance == pytest.approx(100_000.0)
