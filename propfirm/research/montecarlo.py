"""Parallel Monte Carlo over independent challenge attempts.

Spec section 8: report the pass-rate *distribution* across many parallel accounts,
never a single path. One passed challenge is an anecdote; the distribution is the
result. This is simulation's decisive advantage over live trading, and it is what
inoculates against the fallacy that ends real prop careers -- "I passed, therefore
the strategy works."

Runs at the instrument's true 2s tick rate. Coarser sampling was measured to bias
outcomes pessimistically (docs/phase0_findings.md), because honest gap-fills charge
the adverse jump between samples and bigger samples mean bigger fake gaps.
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from propfirm.data.synth_ticks import gbm_ticks
from propfirm.rules.breach import Outcome
from propfirm.rules.ruleset import STRICT_100K, FirmRules
from propfirm.sim.contract import VOL75, ContractSpec
from propfirm.sim.engine import SimEngine

TICK_SECONDS = 2
START_EPOCH = 1_780_000_000


def gbm_path(days: float, seed: int, start_price: float = 49_766.0,
             tick_seconds: int = TICK_SECONDS) -> pd.DataFrame:
    n = int(days * 86400 / tick_seconds)
    prices = gbm_ticks(n, start_price, tick_seconds, np.random.default_rng(seed))
    return pd.DataFrame({"epoch": np.arange(n) * tick_seconds + START_EPOCH,
                         "price": prices})


@dataclass
class TrialResult:
    seed: int
    outcome: str
    final_equity: float
    peak_equity: float
    trades: int
    trading_days: int
    blocked_by: tuple[str, ...] = ()


def _one_trial(args) -> TrialResult:
    seed, days, strategy_factory, spec, rules = args
    ticks = gbm_path(days, seed)
    engine = SimEngine(spec=spec, rules=rules)
    r = engine.run(ticks, strategy_factory(seed))
    return TrialResult(
        seed=seed, outcome=r.outcome.value, final_equity=r.final_equity,
        peak_equity=r.peak_equity, trades=r.trades, trading_days=r.trading_days,
        blocked_by=tuple(r.state.blocked_by),
    )


def run_trials(strategy_factory: Callable[[int], object], n: int = 200,
               days: float = 30.0, spec: ContractSpec = VOL75,
               rules: FirmRules = STRICT_100K, workers: int | None = None,
               seed0: int = 0) -> list[TrialResult]:
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    args = [(seed0 + i, days, strategy_factory, spec, rules) for i in range(n)]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_one_trial, args, chunksize=1))


def summarise(results: list[TrialResult], label: str = "") -> dict:
    n = len(results)
    eq = np.array([r.final_equity for r in results])
    outcomes = [r.outcome for r in results]
    n_pass = sum(o == Outcome.PASSED.value for o in outcomes)
    return {
        "label": label,
        "n": n,
        "p_pass": n_pass / n if n else 0.0,
        "n_pass": n_pass,
        "breached_daily": sum(o == Outcome.BREACHED_DAILY.value for o in outcomes),
        "breached_total": sum(o == Outcome.BREACHED_TOTAL.value for o in outcomes),
        "expired": sum(o == Outcome.EXPIRED.value for o in outcomes),
        "running": sum(o == Outcome.RUNNING.value for o in outcomes),
        "median_equity": float(np.median(eq)),
        "mean_equity": float(eq.mean()),
        "mean_trades": float(np.mean([r.trades for r in results])),
        "blocked_at_target": sum(1 for r in results if r.blocked_by),
    }
