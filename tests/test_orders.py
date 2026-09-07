"""Order types: limit, stop, trailing stop, break-even, partial close (§5)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from propfirm.rules.ruleset import STRICT_100K
from propfirm.sim.engine import SimEngine
from propfirm.sim.orders import OrderType, PendingOrder
from tests.conftest import spec_with

# Zero spread, whole-number geometry keeps the fill arithmetic legible.
SPEC = spec_with(digits=2, point=0.01, spread_points=0.0, stops_level_points=0.0,
                 min_lot=0.001, lot_step=0.001)


def _run(prices, strategy):
    ticks = pd.DataFrame({"epoch": np.arange(len(prices)),
                          "price": [float(p) for p in prices]})
    return SimEngine(spec=SPEC, rules=STRICT_100K).run(ticks, strategy)


class _OnceAtStart:
    """Base helper: act exactly once, on the first tick."""
    def __init__(self):
        self._done = False

    def on_tick(self, ctx):
        if not self._done:
            self._done = True
            self.act(ctx)


# --- PendingOrder trigger logic (unit) --------------------------------------

def test_buy_limit_triggers_on_a_dip_and_fills_at_the_level():
    o = PendingOrder(direction=+1, lots=1.0, order_type=OrderType.LIMIT, trigger=96.0)
    assert o.triggered(prev_mid=97.0, mid=95.0)      # bracketed the level
    assert not o.triggered(prev_mid=100.0, mid=98.0)
    assert o.fill_mid(95.0) == 96.0                  # limit fills at its level


def test_buy_stop_fills_at_the_worse_of_level_and_gap():
    o = PendingOrder(direction=+1, lots=1.0, order_type=OrderType.STOP, trigger=103.0)
    assert o.triggered(prev_mid=102.0, mid=104.0)
    assert o.fill_mid(106.0) == 106.0                # gap past 103 fills at 106, worse
    assert o.fill_mid(103.0) == 103.0


# --- limit / stop entries end to end ----------------------------------------

def test_buy_limit_enters_at_the_limit_price():
    class S(_OnceAtStart):
        def act(self, ctx):
            ctx.buy_limit(1.0, trigger=96.0, sl=90.0, tp=110.0, tag="lim")
    r = _run([100, 99, 98, 97, 95, 97], S())
    assert r.ledger.closed, "the limit should have filled and then force-closed at end"
    assert r.ledger.closed[0].entry_price == pytest.approx(96.0)
    assert r.ledger.closed[0].tag == "lim"


def test_buy_stop_enters_on_a_breakout():
    class S(_OnceAtStart):
        def act(self, ctx):
            ctx.buy_stop(1.0, trigger=103.0, sl=98.0, tp=200.0, tag="stp")
    r = _run([100, 101, 102, 104, 106], S())
    assert r.ledger.closed
    # Filled at the worse of 103 and the 104 available when it triggered.
    assert r.ledger.closed[0].entry_price == pytest.approx(104.0)


def test_unfilled_order_expires():
    class S(_OnceAtStart):
        def act(self, ctx):
            ctx.buy_limit(1.0, trigger=50.0, sl=40.0, tp=60.0, expiry_epoch=3)
    r = _run([100, 101, 102, 103, 104], S())
    assert not r.ledger.closed and not r.ledger.positions   # never filled, then GTD lapse
    assert not r.ledger.pending


# --- trailing stop -----------------------------------------------------------

def test_trailing_stop_locks_in_profit():
    class S(_OnceAtStart):
        def act(self, ctx):
            ctx.buy(1.0, sl=97.0, trail_distance=3.0, tag="trl")
    # Rises to 110 (trail ratchets to 107), then steps back down onto the stop.
    r = _run([100, 103, 106, 109, 110, 108, 107, 105], S())
    t = r.ledger.closed[0]
    assert t.reason == "sl"
    assert t.exit_price == pytest.approx(107.0)      # 110 peak minus the 3-pt trail
    assert t.pnl > 0                                 # a profit, not the -3 original stop


def test_trailing_stop_never_loosens_on_a_gap():
    class S(_OnceAtStart):
        def act(self, ctx):
            ctx.buy(1.0, sl=97.0, trail_distance=3.0)
    # Up to 108 (trails to 105), then a gap straight down to 101.
    r = _run([100, 105, 108, 101, 100], S())
    t = r.ledger.closed[0]
    # The trailed stop was 105; the gap fills at the worse available price, <= 105.
    assert t.exit_price <= 105.0 + 1e-9


# --- break-even shift --------------------------------------------------------

def test_breakeven_moves_the_stop_to_entry():
    class S(_OnceAtStart):
        def act(self, ctx):
            ctx.buy(1.0, sl=95.0, breakeven_trigger=105.0, tag="be")
    # Reaches 106 (arms BE at 100), then falls back through it.
    r = _run([100, 102, 105, 106, 104, 101, 100, 99], S())
    t = r.ledger.closed[0]
    assert t.exit_price == pytest.approx(100.0)      # stopped at entry, not 95
    assert t.pnl == pytest.approx(0.0)


def test_breakeven_with_offset_locks_a_small_gain():
    class S(_OnceAtStart):
        def act(self, ctx):
            ctx.buy(1.0, sl=95.0, breakeven_trigger=105.0, breakeven_offset=2.0)
    r = _run([100, 103, 105, 106, 103, 102, 101], S())
    t = r.ledger.closed[0]
    assert t.exit_price == pytest.approx(102.0)      # entry + 2 offset
    assert t.pnl == pytest.approx(2.0)


# --- partial close -----------------------------------------------------------

def test_partial_close_banks_half_and_keeps_the_rest():
    class S:
        def on_tick(self, ctx):
            if ctx.view.epoch == 0:
                ctx.buy(2.0, sl=90.0, tp=200.0, tag="par")
            elif ctx.view.epoch == 2 and ctx.ledger.positions:
                ctx.close_partial(ctx.ledger.positions[0], 0.5, reason="tp1")
    r = _run([100, 102, 104, 103], S())
    tp1 = [t for t in r.ledger.closed if t.reason == "tp1"]
    assert len(tp1) == 1
    assert tp1[0].lots == pytest.approx(1.0)         # half of 2
    assert tp1[0].pnl == pytest.approx(4.0)          # 1 lot * (104-100)
    assert tp1[0].meta.get("partial") == 0.5


def test_partial_close_that_would_strand_a_dust_lot_closes_fully():
    class S:
        def on_tick(self, ctx):
            if ctx.view.epoch == 0:
                ctx.buy(0.002, sl=90.0, tp=200.0)   # min_lot 0.001; 0.9 leaves 0.0002
            elif ctx.view.epoch == 1 and ctx.ledger.positions:
                ctx.close_partial(ctx.ledger.positions[0], 0.9, reason="dust")
    r = _run([100, 102, 103], S())
    assert not r.ledger.positions                    # closed fully, no dust remainder
    assert any(t.reason == "dust" for t in r.ledger.closed)
