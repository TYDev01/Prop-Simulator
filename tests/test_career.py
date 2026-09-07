"""CareerLedger end-to-end: fees, payouts, and net-after-fees (§5.1, §6)."""
from __future__ import annotations

import pytest

from propfirm.rules.payout import CareerLedger
from propfirm.rules.ruleset import STRICT_100K


def test_fee_charged_per_attempt():
    car = CareerLedger(rules=STRICT_100K)
    car.start_attempt()
    car.start_attempt()
    assert car.total_fees == pytest.approx(1_000.0)   # 2 * $500


def test_payout_applies_profit_split():
    car = CareerLedger(rules=STRICT_100K)
    a = car.start_attempt()
    p = car.record_payout(a, epoch=1, account_profit=8_000.0)
    assert p.trader_share == pytest.approx(6_400.0)    # 80% split
    assert a.gross_paid_out == pytest.approx(6_400.0)
    assert a.net == pytest.approx(5_900.0)             # 6_400 - 500 fee


def test_net_is_payouts_minus_all_fees():
    car = CareerLedger(rules=STRICT_100K)
    winner = car.start_attempt()
    car.record_payout(winner, epoch=1, account_profit=8_000.0)
    car.start_attempt()   # a failed attempt: fee paid, no payout
    assert car.total_payouts == pytest.approx(6_400.0)
    assert car.total_fees == pytest.approx(1_000.0)
    assert car.net == pytest.approx(5_400.0)


def test_summary_probabilities():
    car = CareerLedger(rules=STRICT_100K)
    a = car.start_attempt()
    a.reached_funded = True
    car.record_payout(a, epoch=1, account_profit=5_000.0)
    car.start_attempt()   # neither funded nor paid
    car.start_attempt()

    s = car.summary()
    assert s["attempts"] == 3
    assert s["reached_funded"] == 1
    assert s["reached_payout"] == 1
    assert s["p_funded"] == pytest.approx(1 / 3)
    assert s["p_payout"] == pytest.approx(1 / 3)
    assert s["net"] == pytest.approx(5_000.0 * 0.8 - 3 * 500.0)
    assert s["net_per_attempt"] == pytest.approx(s["net"] / 3)


def test_empty_career_is_zeroed():
    s = CareerLedger(rules=STRICT_100K).summary()
    assert s == {
        "attempts": 0, "reached_funded": 0, "p_funded": 0.0,
        "reached_payout": 0, "p_payout": 0.0, "total_fees": 0.0,
        "total_payouts": 0.0, "net": 0.0, "net_per_attempt": 0.0,
    }
