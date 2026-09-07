"""Seed strategy vs the random-entry control, on identical paths (§9.1).

This is the decisive test, not a formality. §2.2 makes every entry rule on Vol75
zero-expectancy, so a seed strategy whose entries carry no information must produce
the same outcome distribution as random entry with matched risk management. Running
both arms on the *same seeds* is what turns "it passed sometimes" into evidence.

The expected result is that they are indistinguishable, with any small edge to the
seed coming from selectivity (fewer trades => less spread), exactly as §2.3 predicts.
That is a finding, not a failure.

Usage:
    PYTHONPATH=. python scripts/compare_seed.py [N] [--days D] [--tick-seconds S]
        [--risk R] [--rr RR]
"""
from __future__ import annotations

import argparse
import sys

from propfirm.research.montecarlo import run_trials, summarise
from propfirm.strategy.controls import RandomEntryFactory
from propfirm.strategy.seed_mae import ReducedMAEFactory


def _row(s: dict) -> str:
    return (f"{s['label']:<16} p_pass={s['p_pass'] * 100:5.1f}%  "
            f"breach_d={s['breached_daily']:>3}  breach_t={s['breached_total']:>3}  "
            f"expired={s['expired']:>3}  trades~{s['mean_trades']:.1f}  "
            f"med_eq=${s['median_equity']:,.0f}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("n", nargs="?", type=int, default=200)
    p.add_argument("--days", type=float, default=30.0)
    p.add_argument("--tick-seconds", type=int, default=2)
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()

    # Same seeds for both arms: the only difference is how entries are chosen.
    seed_fac = ReducedMAEFactory(risk_pct=args.risk, rr=args.rr)
    rand_fac = RandomEntryFactory(risk_pct=args.risk, rr=args.rr, trades_per_day=3.0)

    print(f"{args.n} trials x {args.days:.0f}d @ {args.tick_seconds}s  "
          f"(risk={args.risk}%, rr={args.rr})\n")

    seed_res = run_trials(seed_fac, n=args.n, days=args.days,
                          tick_seconds=args.tick_seconds)
    rand_res = run_trials(rand_fac, n=args.n, days=args.days,
                          tick_seconds=args.tick_seconds)

    ss = summarise(seed_res, "reduced-MAE")
    sr = summarise(rand_res, "random-entry")
    print(_row(ss))
    print(_row(sr))
    print()

    delta = (ss["p_pass"] - sr["p_pass"]) * 100
    verdict = ("indistinguishable from random (the expected zero-edge result)"
               if abs(delta) < 5 else "a difference worth investigating for artefacts")
    print(f"P(pass) delta (seed - random): {delta:+.1f} pts -> {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
