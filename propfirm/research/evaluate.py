"""Partition-aware evaluation for the research loop.

Scores a strategy factory over a *specific set of seeds* — the mechanism that keeps
discovery, validation, and holdout genuinely out of sample (§9.1). Each seed is one
independent GBM path, so a seed range is a clean experimental partition.

`compare` runs two arms on the same seeds and returns the P(pass) difference with a
two-proportion z-test p-value, so the difference can be fed to the multiple-testing
ledger rather than eyeballed. The z-test uses the normal approximation (math only,
no SciPy needed here).
"""
from __future__ import annotations

import math
from typing import Callable

from propfirm.research.montecarlo import run_trials, summarise


def p_pass_over(factory: Callable[[int], object], seeds: range, days: float = 30.0,
                tick_seconds: int = 2, **kw) -> float:
    """P(pass) for a factory evaluated on exactly `seeds`."""
    res = run_trials(factory, n=len(seeds), days=days, seed0=seeds.start,
                     tick_seconds=tick_seconds, **kw)
    return summarise(res)["p_pass"]


def _two_proportion_p(k1: int, n1: int, k2: int, n2: int) -> float:
    """Two-sided p-value for H0: p1 == p2, normal approximation."""
    if n1 == 0 or n2 == 0:
        return 1.0
    p1, p2 = k1 / n1, k2 / n2
    pool = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = (p1 - p2) / se
    return math.erfc(abs(z) / math.sqrt(2))


def compare(factory_a: Callable[[int], object], factory_b: Callable[[int], object],
            seeds: range, days: float = 30.0, tick_seconds: int = 2,
            label_a: str = "a", label_b: str = "b", **kw) -> dict:
    """Run both arms on identical seeds; return P(pass) each, delta, and a p-value."""
    n = len(seeds)
    res_a = run_trials(factory_a, n=n, days=days, seed0=seeds.start,
                       tick_seconds=tick_seconds, **kw)
    res_b = run_trials(factory_b, n=n, days=days, seed0=seeds.start,
                       tick_seconds=tick_seconds, **kw)
    sa, sb = summarise(res_a), summarise(res_b)
    p_value = _two_proportion_p(sa["n_pass"], n, sb["n_pass"], n)
    return {
        "n": n,
        f"p_pass_{label_a}": sa["p_pass"],
        f"p_pass_{label_b}": sb["p_pass"],
        "delta": sa["p_pass"] - sb["p_pass"],   # a minus b
        "p_value": p_value,
    }
