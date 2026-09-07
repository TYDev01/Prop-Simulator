"""Reduced M.A.E. seed strategy: entry mechanics and structural stops (§7)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from propfirm.research.montecarlo import gbm_path
from propfirm.rules.breach import Outcome
from propfirm.rules.ruleset import STRICT_100K
from propfirm.sim.contract import VOL75
from propfirm.sim.engine import SimEngine
from propfirm.strategy.seed_mae import ReducedMAE


def test_breakout_opens_a_long_with_a_structural_stop():
    # 15 flat bars at 100 (bar = 10s, 1 tick/s), then price steps up to 105 at a
    # bar boundary: a breakout above the channel that also clears the flat bias.
    prices = [100.0] * 150 + [105.0] * 20
    ticks = pd.DataFrame({"epoch": np.arange(len(prices)), "price": prices})

    strat = ReducedMAE(risk_pct=1.0, rr=2.0, bar_seconds=10,
                       donchian_lookback=5, bias_lookback=10, stop_lookback=5,
                       cooldown_bars=0)
    eng = SimEngine(spec=VOL75, rules=STRICT_100K)
    r = eng.run(ticks, strat)

    # The swing low of the flat warmup is 100, so that is both stop and invalidation.
    assert strat.last_invalidation == pytest.approx(100.0)
    assert r.trades >= 1
    trade = r.ledger.closed[0]
    assert trade.direction == +1
    assert trade.tag.startswith("mae:inv=")


def test_no_entry_without_a_breakout():
    # Pure flat data never breaks the channel, so the strategy never trades.
    prices = [100.0] * 2000
    ticks = pd.DataFrame({"epoch": np.arange(len(prices)), "price": prices})
    strat = ReducedMAE(bar_seconds=10, donchian_lookback=5, bias_lookback=10,
                       stop_lookback=5, cooldown_bars=0)
    eng = SimEngine(spec=VOL75, rules=STRICT_100K)
    r = eng.run(ticks, strat)
    assert r.trades == 0


def test_seed_is_deterministic_on_a_fixed_path():
    ticks = gbm_path(days=15.0, seed=9, tick_seconds=30)
    eng = SimEngine(spec=VOL75, rules=STRICT_100K)
    a = eng.run(ticks, ReducedMAE(risk_pct=1.0, rr=2.0))
    b = eng.run(ticks, ReducedMAE(risk_pct=1.0, rr=2.0))
    assert a.final_equity == b.final_equity
    assert a.trades == b.trades
    assert [t.pnl for t in a.ledger.closed] == [t.pnl for t in b.ledger.closed]


def test_seed_reaches_a_terminal_outcome():
    ticks = gbm_path(days=30.0, seed=2, tick_seconds=30)
    eng = SimEngine(spec=VOL75, rules=STRICT_100K)
    r = eng.run(ticks, ReducedMAE())
    assert r.outcome in {Outcome.PASSED, Outcome.BREACHED_DAILY,
                         Outcome.BREACHED_TOTAL, Outcome.EXPIRED}


def test_selectivity_keeps_trade_count_modest():
    # Multi-filter + cooldown is cost control: it should not overtrade.
    ticks = gbm_path(days=30.0, seed=2, tick_seconds=30)
    eng = SimEngine(spec=VOL75, rules=STRICT_100K)
    r = eng.run(ticks, ReducedMAE(cooldown_bars=3))
    assert r.trades < 30
