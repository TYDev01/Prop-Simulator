"""End-to-end research loop on the seed strategy (BUILD_PROMPT §8, §9.1).

Ties the pieces together into one honest experiment:

  1. Partition the seed space into discovery / validation / holdout.
  2. Pre-register a hypothesis ("the reduced-M.A.E. seed beats random entry by
     >= 3 P(pass) points"), with its falsification criterion, *before* testing.
  3. Test it on the DISCOVERY seeds; record the test in the never-reset
     multiple-testing ledger; resolve the hypothesis against its criterion.
  4. Run champion (random entry) vs challenger (seed) on the VALIDATION seeds and
     apply the promotion margin.
  5. Touch the HOLDOUT exactly once to report the champion's out-of-sample P(pass).
  6. Print the researcher's calibration and a per-trade log summary.

The expected outcome is a falsified hypothesis and no promotion — §2.2 says the
seed's entries carry no edge. That is the point: the loop should refuse to promote
noise, and this demonstrates it does.

Usage:
    PYTHONPATH=. python scripts/research_demo.py [N] [--tick-seconds S] [--days D]
"""
from __future__ import annotations

import argparse
import sys

from propfirm.research.champion_challenger import ChampionChallenger, StrategyVersion
from propfirm.research.evaluate import compare, p_pass_over
from propfirm.research.multiple_testing import MultipleTestingLedger
from propfirm.research.partition import HoldoutGuard, Partition
from propfirm.research.preregistration import Hypothesis, PreRegistry
from propfirm.research.trade_log import records_from_ledger, summarise
from propfirm.research.montecarlo import gbm_path
from propfirm.rules.ruleset import STRICT_100K
from propfirm.sim.contract import VOL75
from propfirm.sim.engine import SimEngine
from propfirm.strategy.controls import RandomEntryFactory
from propfirm.strategy.seed_mae import ReducedMAE, ReducedMAEFactory

START_PRICE = 49_766.0
MARGIN = 0.03            # a challenger must beat the champion by 3 P(pass) points


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("n", nargs="?", type=int, default=90, help="total seeds to split")
    p.add_argument("--tick-seconds", type=int, default=10)
    p.add_argument("--days", type=float, default=30.0)
    args = p.parse_args()

    ev = dict(days=args.days, tick_seconds=args.tick_seconds)
    part = Partition.make(args.n, discovery_frac=0.6, validation_frac=0.2)
    seed_fac = ReducedMAEFactory(risk_pct=1.0, rr=2.0)
    rand_fac = RandomEntryFactory(risk_pct=1.0, rr=2.0, trades_per_day=3.0)

    print(f"seeds: discovery {part.discovery}  validation {part.validation}  "
          f"holdout {part.holdout}  @ {args.tick_seconds}s\n")

    # 1-2. Pre-register before touching any data.
    reg = PreRegistry()
    reg.register(Hypothesis(
        id="seed-beats-random",
        description="Reduced-M.A.E. seed beats random entry on P(pass) by >= 3 points",
        metric="p_pass_delta", predicted_effect=MARGIN,
        falsification="delta < 3 points, or wrong sign",
        price_level=START_PRICE, confidence=0.5))

    # 3. Test on discovery, record in the never-reset ledger, resolve.
    mtl = MultipleTestingLedger()
    disc = compare(seed_fac, rand_fac, part.seeds("discovery"),
                   label_a="seed", label_b="random", **ev)
    mtl.record("seed-beats-random", "p_pass_delta", disc["delta"],
               p_value=disc["p_value"], n=disc["n"], price_level=START_PRICE)
    reg.resolve("seed-beats-random", realized_effect=disc["delta"])
    hyp = reg.hypotheses["seed-beats-random"]

    print("DISCOVERY test")
    print(f"  seed   P(pass) = {disc['p_pass_seed'] * 100:5.1f}%")
    print(f"  random P(pass) = {disc['p_pass_random'] * 100:5.1f}%")
    print(f"  delta          = {disc['delta'] * 100:+.1f} pts   p = {disc['p_value']:.3f}")
    print(f"  Bonferroni bar (after {mtl.n_tests} test) = {mtl.bonferroni_bar():.3f}")
    print(f"  hypothesis     = {hyp.outcome.upper()}\n")

    # 4. Champion (random) vs challenger (seed) on validation seeds.
    champ = StrategyVersion(version=0, label="random-entry", factory=rand_fac,
                            rationale="zero-edge baseline control")
    cc = ChampionChallenger(champion=champ, promotion_margin=MARGIN)
    challenger = cc.new_version("reduced-mae", seed_fac,
                                rationale="structural stops + selectivity")
    val_seeds = part.seeds("validation")
    decision = cc.consider(challenger,
                           score_fn=lambda f: p_pass_over(f, val_seeds, **ev),
                           seeds=val_seeds)
    print("VALIDATION promotion test")
    print(f"  champion  (random) P(pass) = {decision['champion_score'] * 100:5.1f}%")
    print(f"  challenger (seed)  P(pass) = {decision['challenger_score'] * 100:5.1f}%")
    print(f"  margin required = {MARGIN * 100:.0f} pts  ->  "
          f"promoted = {decision['promoted']}\n")

    # 5. Touch the holdout exactly once, for the reigning champion only.
    guard = HoldoutGuard(part)
    holdout = guard.take()
    champ_holdout = p_pass_over(cc.champion.factory, holdout, **ev)
    print(f"HOLDOUT (touched once): champion '{cc.champion.label}' "
          f"P(pass) = {champ_holdout * 100:.1f}%\n")

    # 6. Researcher calibration + a per-trade log sample.
    print(f"researcher calibration: {reg.calibration()}")
    ticks = gbm_path(args.days, seed=part.discovery[0], tick_seconds=args.tick_seconds)
    run = SimEngine(spec=VOL75, rules=STRICT_100K).run(ticks, ReducedMAE())
    print(f"sample trade log (seed {part.discovery[0]}): "
          f"{summarise(records_from_ledger(run.ledger))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
