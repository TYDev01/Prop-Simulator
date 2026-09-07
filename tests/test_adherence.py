"""Daily rule-adherence audit and the matched-vol control (§8, §9.1)."""
from __future__ import annotations

import pytest

from propfirm.research.adherence import audit_from_ledger, summarise_audit
from propfirm.research.evaluate import compare_sources
from propfirm.research.montecarlo import gbm_path, matched_gbm
from propfirm.rules.ruleset import STRICT_100K
from propfirm.sim.ledger import ClosedTrade, Ledger
from propfirm.strategy.controls import RandomEntryFactory

DAY = 86_400
BASE = 1_780_000_000


def _ledger(trades, trading_days=None) -> Ledger:
    led = Ledger(spec=None, starting_balance=100_000.0)
    led.closed = list(trades)
    led.trading_days = set(trading_days or [])
    return led


def _trade(day_idx, pnl, *, lots=1.0, stop_dist=None) -> ClosedTrade:
    meta = {"stop_dist": stop_dist} if stop_dist else {}
    epoch = BASE + day_idx * DAY + 3600
    return ClosedTrade(direction=+1, lots=lots, entry_price=100.0, exit_price=100.0,
                       opened_epoch=epoch, closed_epoch=epoch, pnl=pnl, reason="tp",
                       meta=meta)


# --- daily-loss room ---------------------------------------------------------

def test_flags_a_day_that_spends_most_of_its_loss_room():
    # Day-start equity 100k, daily floor 5% = $5,000 room; a -$4,500 day spends 90%.
    led = _ledger([_trade(0, -4_500.0)], trading_days={"d"})
    audit = audit_from_ledger(led, STRICT_100K)
    assert not audit.days[0].ok
    assert any("daily-loss room" in f for f in audit.days[0].flags)


def test_clean_day_has_no_flags():
    led = _ledger([_trade(0, 200.0), _trade(0, -50.0)], trading_days={"d"})
    audit = audit_from_ledger(led, STRICT_100K)
    assert audit.days[0].ok


# --- stop slippage -----------------------------------------------------------

def test_flags_a_stop_that_gaps_past_the_level():
    # pnl / (stop_dist * lots) = -200 / (100 * 1) = -2R, worse than the -1.5R bar.
    led = _ledger([_trade(0, -200.0, stop_dist=100.0)], trading_days={"d"})
    audit = audit_from_ledger(led, STRICT_100K)
    assert any("slipped" in f for f in audit.days[0].flags)


# --- consistency cap ---------------------------------------------------------

def test_flags_consistency_violation_across_days():
    # One dominant winning day: 6000 of 7000 total = 86% > 40% cap.
    led = _ledger([_trade(0, 6_000.0), _trade(1, 1_000.0)],
                  trading_days={"d0", "d1"})
    audit = audit_from_ledger(led, STRICT_100K)
    assert not audit.ok
    assert any("% of profit" in f for f in audit.flags)


def test_flags_min_trading_days_shortfall():
    led = _ledger([_trade(0, 100.0)], trading_days={"only-one"})
    audit = audit_from_ledger(led, STRICT_100K)   # STRICT needs 4
    s = summarise_audit(audit)
    assert s["min_trading_days_met"] is False
    assert any("trading days" in f for f in audit.flags)


def test_day_start_equity_compounds_across_days():
    # Day 0 makes +$10k; day 1 must start from $110k, so its room is 5% of that.
    led = _ledger([_trade(0, 10_000.0), _trade(1, -100.0)],
                  trading_days={"d0", "d1"})
    audit = audit_from_ledger(led, STRICT_100K)
    assert audit.days[1].day_start_equity == pytest.approx(110_000.0)
    assert audit.days[1].daily_loss_room == pytest.approx(5_500.0)


# --- matched-vol control null ------------------------------------------------

def test_matched_gbm_control_shows_no_difference():
    # One strategy across two independent matched-vol GBM draws: the null must hold.
    d = compare_sources(RandomEntryFactory(risk_pct=1.0, rr=2.0, trades_per_day=3.0),
                        gbm_path, matched_gbm, seeds=range(0, 16),
                        days=15.0, tick_seconds=120, label_a="src_a", label_b="src_b")
    assert d["n"] == 16
    assert d["p_value"] > 0.05          # statistically indistinguishable, as required
