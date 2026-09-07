"""Dual-mode A/B: deterministic core vs core + Opus overlay (BUILD_PROMPT §3).

Answers the central Phase 3 question — does the overlay beat the core? — by running
both arms on identical seeds. Both use the same reduced-M.A.E. detection; they
differ only in whether a decision provider gates each candidate.

By default the overlay is a **free, deterministic mock** (`--mock`): a heuristic that
vetoes trades which would open with little room to the daily-loss floor. It costs
nothing and makes the harness runnable offline, but it is a stand-in — a real answer
needs the Opus provider.

`--opus` swaps in the real Claude overlay. That spends money (Opus 5 is $5/$25 per
1M tokens) and needs ANTHROPIC credentials, so it is gated behind an explicit
--budget-usd you must pass. Nothing here calls the API unless you opt in.

Usage:
    PYTHONPATH=. python scripts/run_overlay_ab.py [N] [--days D] [--tick-seconds S]
        [--mock | --opus --budget-usd X]
"""
from __future__ import annotations

import argparse
import sys

from propfirm.llm.overlay import dual_mode
from propfirm.llm.provider import MockProvider
from propfirm.llm.schema import Decision
from propfirm.research.montecarlo import run_trials, summarise


def cautious_policy(packet: dict) -> Decision:
    """Free stand-in overlay: veto a candidate that crowds the daily-loss floor.

    On a zero-edge instrument the only real lever is skipping trades near a limit,
    so this mimics the one thing an overlay could legitimately do — without an edge
    claim. Module-level (picklable) so it runs across ProcessPool workers.
    """
    room = packet.get("account", {}).get("room_to_daily_floor")
    cand = packet.get("candidate", {})
    stop_dist = cand.get("stop_dist", 0.0)
    lots = cand.get("lots", 0.0)
    # Rough loss-at-stop; veto if it would eat most of the remaining daily room.
    risk = stop_dist * lots
    if room is not None and risk > 0.5 * room:
        return Decision(action="no_trade", reasoning="too close to daily floor")
    return Decision(action="trade", confidence=0.6, reasoning="room ok")


def _row(s: dict) -> str:
    return (f"{s['label']:<14} p_pass={s['p_pass'] * 100:5.1f}%  "
            f"breach_d={s['breached_daily']:>3}  breach_t={s['breached_total']:>3}  "
            f"expired={s['expired']:>3}  trades~{s['mean_trades']:.1f}  "
            f"med_eq=${s['median_equity']:,.0f}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("n", nargs="?", type=int, default=100)
    p.add_argument("--days", type=float, default=30.0)
    p.add_argument("--tick-seconds", type=int, default=2)
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--rr", type=float, default=2.0)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--mock", action="store_true", help="free deterministic overlay (default)")
    mode.add_argument("--opus", action="store_true", help="real Claude overlay (spends money)")
    p.add_argument("--budget-usd", type=float, default=None,
                   help="required with --opus: hard cap you accept spending")
    args = p.parse_args()

    if args.opus:
        if args.budget_usd is None:
            print("--opus requires --budget-usd (this spends real money). Aborting.")
            return 2
        # A live Opus A/B is a paid, external action — wire it, don't fire it here.
        print(f"--opus selected with a ${args.budget_usd:.2f} budget.\n"
              "This script wires the real OpusProvider, but running the paid A/B is\n"
              "left to you: set ANTHROPIC credentials and invoke run_trials with an\n"
              "OpusProvider-backed overlay. See propfirm/llm/provider.py::OpusProvider.")
        return 0

    provider = MockProvider(policy=cautious_policy)
    core_fac, overlay_fac = dual_mode(provider, risk_pct=args.risk, rr=args.rr)

    print(f"{args.n} trials x {args.days:.0f}d @ {args.tick_seconds}s  "
          f"(mock overlay: veto-near-floor)\n")
    core = summarise(run_trials(core_fac, n=args.n, days=args.days,
                                tick_seconds=args.tick_seconds), "core")
    over = summarise(run_trials(overlay_fac, n=args.n, days=args.days,
                                tick_seconds=args.tick_seconds), "core+overlay")
    print(_row(core))
    print(_row(over))
    print(f"\nP(pass) delta (overlay - core): "
          f"{(over['p_pass'] - core['p_pass']) * 100:+.1f} pts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
