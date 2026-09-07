"""Headline metric: expected 12-month net after fees (BUILD_PROMPT §10, §6).

Runs N parallel careers through the whole fee -> P1 -> P2 -> funded -> payout
loop and reports the net-after-fees distribution. This is the number the project
exists to produce: not "can it pass" but "does a year of attempts clear the fees".

Usage:
    PYTHONPATH=. python scripts/run_campaign.py [N] [--risk R] [--rr RR]
        [--trades-per-day T] [--tick-seconds S] [--horizon-days D] [--out FILE]

Defaults reproduce the best measured configuration (1% risk, 1:2 RR, 3 trades/day)
at the instrument's true 2s tick rate. Coarser --tick-seconds runs faster and biases
net downward (bigger gap-fills), so treat a coarse result as a conservative floor.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from propfirm.rules.campaign import run_campaign, summarise_campaign
from propfirm.rules.ruleset import STRICT_100K
from propfirm.strategy.controls import RandomEntryFactory


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("n", nargs="?", type=int, default=200, help="parallel careers")
    p.add_argument("--risk", type=float, default=1.0, help="risk %% per trade")
    p.add_argument("--rr", type=float, default=2.0, help="reward-to-risk")
    p.add_argument("--trades-per-day", type=float, default=3.0)
    p.add_argument("--tick-seconds", type=int, default=2)
    p.add_argument("--horizon-days", type=float, default=365.0)
    p.add_argument("--seed0", type=int, default=0)
    p.add_argument("--out", type=str, default=None, help="write the summary JSON here")
    args = p.parse_args()

    factory = RandomEntryFactory(risk_pct=args.risk, rr=args.rr,
                                 trades_per_day=args.trades_per_day)

    label = (f"random-entry risk={args.risk}% rr={args.rr} "
             f"tpd={args.trades_per_day} @ {args.tick_seconds}s")
    print(f"Running {args.n} careers over {args.horizon_days:.0f} days -- {label}",
          flush=True)
    t0 = time.time()
    results = run_campaign(factory, n=args.n, rules=STRICT_100K,
                           horizon_days=args.horizon_days,
                           tick_seconds=args.tick_seconds, seed0=args.seed0)
    summary = summarise_campaign(results, label=label)
    summary["elapsed_s"] = round(time.time() - t0, 1)
    summary["tick_seconds"] = args.tick_seconds
    summary["horizon_days"] = args.horizon_days

    print("\n" + "=" * 60)
    print(f"careers                : {summary['n']}")
    print(f"reached funded         : {summary['p_reached_funded'] * 100:5.1f}%")
    print(f"reached a payout       : {summary['p_reached_payout'] * 100:5.1f}%")
    print(f"profitable (net > 0)   : {summary['p_profitable'] * 100:5.1f}%")
    print(f"mean attempts/career   : {summary['mean_attempts']:.1f}")
    print(f"mean fees/career       : ${summary['mean_fees']:,.0f}")
    print(f"mean payouts/career    : ${summary['mean_payouts']:,.0f}")
    print("-" * 60)
    print(f"EXPECTED 12-MO NET      : ${summary['expected_net']:,.0f}")
    print(f"  median               : ${summary['median_net']:,.0f}")
    print(f"  p10 .. p90           : ${summary['net_p10']:,.0f} .. "
          f"${summary['net_p90']:,.0f}")
    print(f"elapsed                : {summary['elapsed_s']}s")
    print("=" * 60)

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(summary, fh, indent=2)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
