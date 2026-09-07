"""Engine determinism and margin stop-out enforcement (§5.1, REMAINING.md §1.2)."""
from __future__ import annotations

import pytest

from propfirm.research.montecarlo import gbm_path
from propfirm.rules.breach import Outcome
from propfirm.rules.ruleset import STRICT_100K
from propfirm.sim.contract import VOL75
from propfirm.sim.engine import SimEngine
from propfirm.sim.ledger import Ledger, Position
from propfirm.strategy.controls import RandomEntry
from tests.conftest import spec_with


# --- determinism -------------------------------------------------------------

def test_identical_inputs_replay_bit_identically():
    ticks = gbm_path(days=20.0, seed=7)

    def run_once():
        eng = SimEngine(spec=VOL75, rules=STRICT_100K)
        return eng.run(ticks, RandomEntry(risk_pct=1.0, rr=2.0, seed=7))

    a, b = run_once(), run_once()
    assert a.final_equity == b.final_equity          # exact, not approx
    assert a.outcome == b.outcome
    assert a.trades == b.trades
    assert [t.pnl for t in a.ledger.closed] == [t.pnl for t in b.ledger.closed]


def test_run_reaches_a_terminal_outcome():
    ticks = gbm_path(days=30.0, seed=3)
    eng = SimEngine(spec=VOL75, rules=STRICT_100K)
    r = eng.run(ticks, RandomEntry(risk_pct=1.0, seed=3))
    assert r.outcome in {Outcome.PASSED, Outcome.BREACHED_DAILY,
                         Outcome.BREACHED_TOTAL, Outcome.EXPIRED}


# --- margin stop-out ---------------------------------------------------------

# leverage 2 makes used margin large enough that a drawdown can pierce the
# stop-out level before the (unused here) prop rules would.
STOPOUT_SPEC = spec_with(digits=2, point=0.01, spread_points=0.0,
                         stops_level_points=0.0, leverage=2.0,
                         min_lot=0.001, lot_step=0.001, max_lot=100.0,
                         stop_out_level_pct=50.0)


def _engine_with_open_position(balance: float):
    eng = SimEngine(spec=STOPOUT_SPEC, rules=STRICT_100K)
    led = Ledger(spec=STOPOUT_SPEC, starting_balance=balance)
    led.open(Position(direction=+1, lots=10.0, entry_price=100.0, opened_epoch=0))
    return eng, led


def test_stop_out_liquidates_when_margin_level_breached():
    eng, led = _engine_with_open_position(balance=300.0)
    # margin level at mark 90: equity 200 / used 450 = 44% < 50% -> liquidate.
    assert eng._enforce_stop_out(led, mid=90.0, epoch=1) is True
    assert led.positions == []
    assert led.closed[-1].reason == "stop_out"


def test_stop_out_dormant_while_margin_healthy():
    eng, led = _engine_with_open_position(balance=300.0)
    # margin level at mark 100: equity 300 / used 500 = 60% > 50% -> no action.
    assert eng._enforce_stop_out(led, mid=100.0, epoch=1) is False
    assert len(led.positions) == 1
