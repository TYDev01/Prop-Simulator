"""Research loop: trade log, partition, pre-registration, multiple testing,
champion/challenger (BUILD_PROMPT §8, §9.1)."""
from __future__ import annotations

import pytest

from propfirm.research.champion_challenger import ChampionChallenger, StrategyVersion
from propfirm.research.evaluate import _two_proportion_p
from propfirm.research.multiple_testing import MultipleTestingLedger
from propfirm.research.partition import HoldoutGuard, HoldoutViolation, Partition
from propfirm.research.preregistration import (
    Hypothesis,
    PreRegistry,
    directional_confirmed,
)
from propfirm.research.trade_log import TradeRecord, records_from_ledger, summarise
from propfirm.research.montecarlo import gbm_path
from propfirm.rules.ruleset import STRICT_100K
from propfirm.sim.contract import VOL75
from propfirm.sim.engine import SimEngine
from propfirm.strategy.seed_mae import ReducedMAE


# --- trade log ---------------------------------------------------------------

def test_trade_log_captures_meta_and_outcome():
    ticks = gbm_path(30.0, seed=4, tick_seconds=30)
    r = SimEngine(spec=VOL75, rules=STRICT_100K).run(ticks, ReducedMAE(risk_pct=1.0))
    recs = records_from_ledger(r.ledger)
    assert recs, "seed should have traded on this path"
    t = recs[0]
    assert "invalidation" in t.meta and "stop_dist" in t.meta
    # A stop-out loses about 1R — and never less: the fill is the worse of the
    # level and the available price, so spread/gap push it just past -1R.
    sl_hits = [x for x in recs if x.reason == "sl"]
    assert sl_hits
    assert sl_hits[0].r_multiple == pytest.approx(-1.0, abs=0.05)
    assert sl_hits[0].r_multiple <= -1.0 + 1e-9


def test_trade_log_summary_of_empty_is_zeroed():
    s = summarise([])
    assert s["n"] == 0 and s["expectancy"] == 0.0


def test_trade_log_summary_stats():
    recs = [
        TradeRecord(0, 1, +1, 1.0, 100, 110, 200.0, "tp", 0, 0, 2.0),
        TradeRecord(0, 1, +1, 1.0, 100, 95, -100.0, "sl", 0, 0, -1.0),
    ]
    s = summarise(recs)
    assert s["n"] == 2 and s["win_rate"] == pytest.approx(0.5)
    assert s["expectancy"] == pytest.approx(50.0)
    assert s["mean_r"] == pytest.approx(0.5)


# --- partition & holdout guard ----------------------------------------------

def test_partition_is_disjoint_and_covers():
    p = Partition.make(100, discovery_frac=0.6, validation_frac=0.2)
    assert list(p.seeds("discovery")) == list(range(0, 60))
    assert list(p.seeds("validation")) == list(range(60, 80))
    assert list(p.seeds("holdout")) == list(range(80, 100))
    assert not p.overlaps()


def test_holdout_can_be_taken_once_only():
    guard = HoldoutGuard(Partition.make(100))
    assert not guard.spent
    seeds = guard.take()
    assert list(seeds) == list(range(80, 100)) and guard.spent
    with pytest.raises(HoldoutViolation):
        guard.take()


# --- pre-registration & calibration -----------------------------------------

def _hyp(pid, predicted, conf=None):
    return Hypothesis(id=pid, description="d", metric="p_pass_delta",
                      predicted_effect=predicted, falsification="delta<=0",
                      price_level=50000.0, confidence=conf)


def test_directional_confirmation_rule():
    assert directional_confirmed(0.03, 0.02) is True        # same sign, big enough
    assert directional_confirmed(0.03, 0.005) is False      # too small
    assert directional_confirmed(0.03, -0.04) is False      # wrong sign


def test_cannot_register_resolved_or_duplicate():
    reg = PreRegistry()
    reg.register(_hyp("h1", 0.03))
    with pytest.raises(ValueError):
        reg.register(_hyp("h1", 0.03))                       # duplicate id
    resolved = _hyp("h2", 0.03)
    resolved.outcome = "confirmed"
    with pytest.raises(ValueError):
        reg.register(resolved)                               # already resolved


def test_calibration_hit_rate_and_brier():
    reg = PreRegistry()
    reg.register(_hyp("h1", 0.03, conf=0.9))
    reg.register(_hyp("h2", 0.03, conf=0.8))
    reg.resolve("h1", 0.02)          # confirmed (same sign, big enough)
    reg.resolve("h2", -0.05)         # falsified (wrong sign)
    cal = reg.calibration()
    assert cal["resolved"] == 2 and cal["hit_rate"] == pytest.approx(0.5)
    # brier = mean((0.9-1)^2, (0.8-0)^2) = mean(0.01, 0.64) = 0.325
    assert cal["brier"] == pytest.approx(0.325)


# --- multiple-testing ledger -------------------------------------------------

def test_bonferroni_bar_tightens_with_every_test():
    led = MultipleTestingLedger()
    assert led.bonferroni_bar(0.05) == 0.05        # no tests yet
    for i in range(5):
        led.record(f"h{i}", "m", effect=0.0, p_value=0.04)
    assert led.n_tests == 5
    assert led.bonferroni_bar(0.05) == pytest.approx(0.01)
    # p=0.04 was "significant" alone but not after 5 tests.
    assert not led.survives_bonferroni(0.04, 0.05)
    assert led.survives_bonferroni(0.008, 0.05)


def test_bh_threshold_and_persistence(tmp_path):
    led = MultipleTestingLedger()
    for p in (0.001, 0.2, 0.5, 0.7):
        led.record("h", "m", 0.0, p_value=p)
    # Only the very small p should survive BH at alpha=0.05 across 4 tests.
    disc = led.discoveries(0.05)
    assert [e.p_value for e in disc] == [0.001]
    path = tmp_path / "mtl.jsonl"
    led.write_jsonl(str(path))
    reloaded = MultipleTestingLedger.load_jsonl(str(path))
    assert reloaded.n_tests == 4                    # count survives a restart


# --- champion / challenger ---------------------------------------------------

def test_promotion_requires_the_full_margin():
    champ = StrategyVersion(version=0, label="champion", factory=object())
    cc = ChampionChallenger(champion=champ, promotion_margin=0.02)
    assert cc.should_promote(0.20, 0.23) is True    # +0.03 clears +0.02
    assert cc.should_promote(0.20, 0.215) is False  # +0.015 does not


def test_consider_promotes_and_versions():
    champ = StrategyVersion(version=0, label="champion", factory="champ")
    cc = ChampionChallenger(champion=champ, promotion_margin=0.02)
    chall = cc.new_version("challenger", factory="chall", rationale="better entries")
    assert chall.version == 1 and chall.parent == 0

    # Score function: challenger clearly wins.
    scores = {"champ": 0.20, "chall": 0.30}
    decision = cc.consider(chall, score_fn=lambda f: scores[f], seeds=range(60, 80))
    assert decision["promoted"] is True
    assert cc.champion.version == 1
    assert len(cc.history) == 2
    assert chall.evidence["promotion_test"]["challenger_score"] == 0.30


def test_consider_keeps_champion_when_margin_not_met():
    champ = StrategyVersion(version=0, label="champion", factory="champ")
    cc = ChampionChallenger(champion=champ, promotion_margin=0.05)
    chall = cc.new_version("challenger", factory="chall")
    scores = {"champ": 0.20, "chall": 0.22}         # only +0.02, margin is 0.05
    decision = cc.consider(chall, score_fn=lambda f: scores[f], seeds=range(60, 80))
    assert decision["promoted"] is False
    assert cc.champion.version == 0                 # unchanged


# --- two-proportion test -----------------------------------------------------

def test_two_proportion_p_extremes():
    assert _two_proportion_p(50, 100, 50, 100) == pytest.approx(1.0)   # identical
    assert _two_proportion_p(90, 100, 10, 100) < 0.001                 # far apart
