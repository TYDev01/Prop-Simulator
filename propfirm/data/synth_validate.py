"""Statistical validation of a synthetic tick path (BUILD_PROMPT §4/§5).

OHLC fidelity alone does not make a synthetic path faithful — two earlier synthesis
attempts passed on OHLC and were rejected here for manufacturing momentum (lag-1
autocorrelation) or fat tails (kurtosis). These are the checks that matter for a
tick-level simulator: whether the *increments* look like the true driftless process.

`tick_stats` computes the diagnostics; `compare_paths` judges a candidate path
against a reference (or against the theoretical Vol75 values) with explicit
tolerances, so "the M15 replay is faithful" is a pass/fail statement, not a vibe.
"""
from __future__ import annotations

import numpy as np

from propfirm.data.synth_ticks import per_tick_sigma


def tick_stats(prices, tick_seconds: float) -> dict:
    """Increment diagnostics for a price path: sigma, lag-1 autocorr, up-tick share,
    excess kurtosis, plus the theoretical per-tick sigma for reference."""
    prices = np.asarray(prices, dtype=float)
    logret = np.diff(np.log(prices))
    n = len(logret)
    if n < 2:
        return {"n_ticks": len(prices), "sigma": 0.0, "autocorr1": 0.0,
                "uptick_frac": 0.0, "kurtosis": 0.0,
                "theoretical_sigma": per_tick_sigma(tick_seconds)}
    a, b = logret[:-1], logret[1:]
    sa, sb = a.std(), b.std()
    autocorr1 = float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb)) \
        if sa > 0 and sb > 0 else 0.0
    sigma = float(logret.std())
    # Excess kurtosis (Gaussian ⇒ ~0) without a SciPy dependency.
    m = logret.mean()
    var = ((logret - m) ** 2).mean()
    kurt = float(((logret - m) ** 4).mean() / var ** 2 - 3.0) if var > 0 else 0.0
    return {
        "n_ticks": len(prices),
        "sigma": sigma,
        "autocorr1": autocorr1,
        "uptick_frac": float((np.diff(prices) > 0).mean()),
        "kurtosis": kurt,
        "theoretical_sigma": per_tick_sigma(tick_seconds),
    }


def compare_paths(candidate, tick_seconds: float, reference=None,
                  sigma_tol: float = 0.15, autocorr_tol: float = 0.05,
                  uptick_tol: float = 0.03, kurtosis_tol: float = 0.5) -> dict:
    """Judge a candidate path. Against `reference` if given, else against theory.

    sigma is compared as a relative error to the reference (or theoretical) sigma;
    autocorr and kurtosis against 0; up-tick share against 0.5. Returns per-check
    pass flags and an overall verdict.
    """
    cand = tick_stats(candidate, tick_seconds)
    ref = tick_stats(reference, tick_seconds) if reference is not None else None
    ref_sigma = ref["sigma"] if ref else cand["theoretical_sigma"]

    sigma_rel = abs(cand["sigma"] - ref_sigma) / ref_sigma if ref_sigma else 0.0
    checks = {
        "sigma": sigma_rel <= sigma_tol,
        "autocorr1": abs(cand["autocorr1"]) <= autocorr_tol,
        "uptick_frac": abs(cand["uptick_frac"] - 0.5) <= uptick_tol,
        "kurtosis": abs(cand["kurtosis"]) <= kurtosis_tol,
    }
    return {
        "candidate": cand,
        "reference": ref,
        "sigma_rel_error": sigma_rel,
        "checks": checks,
        "passed": all(checks.values()),
    }


def ohlc_error(bars, synth: "pd.DataFrame", bar_seconds: int) -> dict:
    """Mean absolute O/H/L/C error between source bars and re-aggregated synth ticks."""
    from propfirm.data.bars import ticks_to_bars

    got = ticks_to_bars(synth[["epoch", "price"]], bar_seconds)
    merged = bars.merge(got, on="epoch", suffixes=("_src", "_syn"))
    if merged.empty:
        return {"n": 0}
    err = {c: float((merged[f"{c}_src"] - merged[f"{c}_syn"]).abs().mean())
           for c in ("open", "high", "low", "close")}
    err["n"] = len(merged)
    return err
