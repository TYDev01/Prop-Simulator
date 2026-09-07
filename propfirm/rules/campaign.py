"""Career campaign: the metric the whole project exists to produce (§6, §10).

A single passed challenge is an anecdote. The deliverable is what a *campaign*
nets after fees over a fixed horizon, across the attempts that mostly fail:

    fee -> phase 1 -> phase 2 -> funded -> payout cycles -> breach -> buy another

This wires the `CareerLedger` (previously implemented but uncalled, REMAINING.md
§2.3) to the engine and reports the headline **expected 12-month net after fees**,
as a distribution across many parallel careers rather than a single lucky path.

Modelling choices, kept deliberately honest:

  * Each phase and each funded payout cycle is a fresh account at the base size.
    Prop evaluations do not carry profit between phases; funded profit is withdrawn
    at each cycle, so the equity-trailing drawdown resets to the base too.
  * Calendar time is a budget. Every phase/cycle consumes the days it actually took
    (to pass, to breach, or the full window), drawn from the engine's elapsed_days.
    The career ends when the horizon is spent.
  * A breach in any phase ends that attempt; a breach while funded ends the funded
    account. Either way, if time remains, another challenge fee is paid.
  * Vol75 is a driftless GBM, so an independent GBM path per phase/cycle is the real
    process conditioned on nothing (see propfirm/data/synth_ticks.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from propfirm.research.montecarlo import gbm_path
from propfirm.rules.breach import Outcome
from propfirm.rules.payout import CareerLedger
from propfirm.rules.ruleset import STRICT_100K, FirmRules
from propfirm.sim.contract import VOL75, ContractSpec
from propfirm.sim.engine import SimEngine

HORIZON_DAYS = 365.0
PHASE1, PHASE2, FUNDED = 0, 1, 2


def _sub_seed(career_seed: int, counter: int) -> int:
    """A reproducible, independent seed per sub-run within a career."""
    return int(np.random.default_rng([career_seed, counter]).integers(1, 2**31 - 1))


def run_career(strategy_factory: Callable[[int], object], seed: int,
               rules: FirmRules = STRICT_100K, spec: ContractSpec = VOL75,
               horizon_days: float = HORIZON_DAYS, tick_seconds: int = 2) -> CareerLedger:
    """Simulate one trader's whole campaign over `horizon_days`.

    `strategy_factory(seed) -> Strategy` is called once per phase/cycle with an
    independent seed, so entry randomness varies while risk parameters stay fixed.

    `tick_seconds` sets replay resolution. The instrument's true rate is 2s and
    P(pass) was measured there; coarser sampling runs faster but biases outcomes
    pessimistically (bigger gap-fills), so it under-states net rather than
    flattering it (docs/phase0_findings.md).
    """
    car = CareerLedger(rules=rules)
    engine = SimEngine(spec=spec, rules=rules)
    time_used = 0.0
    counter = 0
    # A run needs at least a couple of ticks to be meaningful; below this the
    # remaining horizon is effectively spent.
    min_days = 2.0 * tick_seconds / 86400.0

    def play(phase_index: int, budget_days: float):
        nonlocal time_used, counter
        s = _sub_seed(seed, counter)
        counter += 1
        ticks = gbm_path(budget_days, s, tick_seconds=tick_seconds)
        r = engine.run(ticks, strategy_factory(s), phase_index=phase_index,
                       starting_balance=rules.account_size)
        time_used += r.elapsed_days
        return r

    while time_used < horizon_days - min_days:
        attempt = car.start_attempt()

        # --- Evaluation phases 1 and 2 --------------------------------------
        passed_evaluation = True
        for phase_index in (PHASE1, PHASE2):
            phase = rules.phase(phase_index)
            budget = min(phase.max_days, horizon_days - time_used)
            if budget < min_days:
                passed_evaluation = False
                break
            r = play(phase_index, budget)
            attempt.phase_outcomes.append((phase.name, r.outcome))
            if r.outcome is not Outcome.PASSED:
                passed_evaluation = False
                break
        if not passed_evaluation:
            continue  # attempt failed; the outer loop buys another if time remains

        # --- Funded: payout cycles until a breach or the horizon ------------
        attempt.reached_funded = True
        while time_used < horizon_days - min_days:
            cycle = min(rules.payout_cycle_days, horizon_days - time_used)
            if cycle < min_days:
                break
            r = play(FUNDED, cycle)
            if r.outcome in (Outcome.BREACHED_DAILY, Outcome.BREACHED_TOTAL):
                attempt.phase_outcomes.append(("funded", r.outcome))
                break  # funded account lost; the outer loop buys another
            profit = r.final_equity - rules.account_size
            if profit > 0:
                car.record_payout(attempt, epoch=int(time_used * 86400),
                                  account_profit=profit)
        # funded ended (breach or horizon); outer loop decides whether to continue

    return car


# --- parallel campaign -------------------------------------------------------

@dataclass
class CareerResult:
    seed: int
    net: float
    total_fees: float
    total_payouts: float
    attempts: int
    reached_funded: int
    reached_payout: int


def _one_career(args) -> CareerResult:
    seed, strategy_factory, rules, spec, horizon, tick_seconds = args
    car = run_career(strategy_factory, seed, rules, spec, horizon, tick_seconds)
    s = car.summary()
    return CareerResult(
        seed=seed, net=s["net"], total_fees=s["total_fees"],
        total_payouts=s["total_payouts"], attempts=s["attempts"],
        reached_funded=s["reached_funded"], reached_payout=s["reached_payout"],
    )


def run_campaign(strategy_factory: Callable[[int], object], n: int = 200,
                 rules: FirmRules = STRICT_100K, spec: ContractSpec = VOL75,
                 horizon_days: float = HORIZON_DAYS, tick_seconds: int = 2,
                 workers: int | None = None, seed0: int = 0) -> list[CareerResult]:
    import os
    from concurrent.futures import ProcessPoolExecutor

    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    args = [(seed0 + i, strategy_factory, rules, spec, horizon_days, tick_seconds)
            for i in range(n)]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_one_career, args, chunksize=1))


def summarise_campaign(results: list[CareerResult], label: str = "") -> dict:
    n = len(results)
    net = np.array([r.net for r in results], dtype=float)
    funded = sum(1 for r in results if r.reached_funded > 0)
    paid = sum(1 for r in results if r.reached_payout > 0)
    profitable = int((net > 0).sum())
    return {
        "label": label,
        "n": n,
        "expected_net": float(net.mean()) if n else 0.0,
        "median_net": float(np.median(net)) if n else 0.0,
        "net_p10": float(np.percentile(net, 10)) if n else 0.0,
        "net_p90": float(np.percentile(net, 90)) if n else 0.0,
        "std_net": float(net.std()) if n else 0.0,
        "p_profitable": profitable / n if n else 0.0,
        "p_reached_funded": funded / n if n else 0.0,
        "p_reached_payout": paid / n if n else 0.0,
        "mean_attempts": float(np.mean([r.attempts for r in results])) if n else 0.0,
        "mean_fees": float(np.mean([r.total_fees for r in results])) if n else 0.0,
        "mean_payouts": float(np.mean([r.total_payouts for r in results])) if n else 0.0,
    }
