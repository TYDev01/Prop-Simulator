"""Career campaign: progression, fee accounting, and headline aggregation (§2.3)."""
from __future__ import annotations

import pytest

from propfirm.rules.breach import Outcome
from propfirm.rules.campaign import run_career, summarise_campaign, CareerResult
from propfirm.strategy.controls import NoTrade, RandomEntryFactory


def _no_trade_factory(seed):
    return NoTrade()


# --- NoTrade: a fully determined career ------------------------------------

def test_no_trade_career_only_pays_fees_and_expires():
    # A strategy that never trades can never hit a target, so every attempt
    # expires at the phase-1 deadline and the career just bleeds fees.
    car = run_career(_no_trade_factory, seed=0, horizon_days=70.0, tick_seconds=120)
    s = car.summary()

    assert s["attempts"] >= 2
    assert s["reached_funded"] == 0
    assert s["total_payouts"] == 0.0
    assert s["net"] == pytest.approx(-500.0 * s["attempts"])
    # Every recorded phase outcome is an expiry; none ever reaches phase 2.
    for a in car.attempts:
        for _, outcome in a.phase_outcomes:
            assert outcome is Outcome.EXPIRED


def test_career_never_exceeds_the_horizon_by_much():
    car = run_career(_no_trade_factory, seed=1, horizon_days=70.0, tick_seconds=120)
    # Attempts are 30-day phase-1 expiries, so ~2-3 fit in 70 days, not dozens.
    assert 2 <= len(car.attempts) <= 4


# --- determinism -------------------------------------------------------------

def test_career_is_reproducible_for_a_fixed_seed():
    f = RandomEntryFactory(risk_pct=1.0, rr=2.0, trades_per_day=3.0)
    a = run_career(f, seed=5, horizon_days=90.0, tick_seconds=120).summary()
    b = run_career(f, seed=5, horizon_days=90.0, tick_seconds=120).summary()
    assert a == b


# --- aggregation -------------------------------------------------------------

def test_summarise_campaign_matches_hand_computed_stats():
    rs = [
        CareerResult(seed=0, net=1000.0, total_fees=500.0, total_payouts=1500.0,
                     attempts=1, reached_funded=1, reached_payout=1),
        CareerResult(seed=1, net=-1500.0, total_fees=1500.0, total_payouts=0.0,
                     attempts=3, reached_funded=0, reached_payout=0),
    ]
    s = summarise_campaign(rs, label="t")
    assert s["n"] == 2
    assert s["expected_net"] == pytest.approx(-250.0)      # mean(1000, -1500)
    assert s["p_profitable"] == pytest.approx(0.5)
    assert s["p_reached_funded"] == pytest.approx(0.5)
    assert s["p_reached_payout"] == pytest.approx(0.5)
    assert s["mean_attempts"] == pytest.approx(2.0)
    assert s["mean_fees"] == pytest.approx(1000.0)


def test_summarise_empty_campaign_is_zeroed():
    s = summarise_campaign([], label="empty")
    assert s["n"] == 0
    assert s["expected_net"] == 0.0
    assert s["p_reached_funded"] == 0.0
