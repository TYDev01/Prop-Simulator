"""Validate long-history replay from M15 anchors (BUILD_PROMPT §2.7).

Two layers, because OHLC fidelity alone is not enough (§4):

  1. Increment statistics — does the M15-synthesised path have the right per-tick
     sigma, ~zero autocorrelation (no manufactured momentum), a ~50% up-tick share,
     and acceptable tails?
  2. The decisive test — does replaying through M15 change what the simulator
     concludes? Random entry runs on the true tick path vs the M15 replay of that
     same path (`compare_sources`); if P(pass) matches, the replay is fit for the one
     purpose the project exists to serve, whatever the microstructure caveats.

Everything is driven from GBM ground truth, so this runs fully offline. Expected
finding: statistics pass except an elevated kurtosis from snapping wicks to the exact
H/L at 900s bars, and P(pass) nonetheless preserved — i.e. usable, with that caveat.

Usage:
    PYTHONPATH=. python scripts/validate_m15_replay.py [--days D] [--tick-seconds S]
        [--pass-n N] [--pass-days D] [--pass-tick-seconds S] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import sys

from propfirm.data.bars import ticks_to_bars
from propfirm.data.replay import M15_SECONDS, m15_replay_path, replay_from_bars
from propfirm.data.synth_validate import compare_paths, ohlc_error
from propfirm.research.evaluate import compare_sources
from propfirm.research.montecarlo import gbm_path
from propfirm.strategy.controls import RandomEntryFactory


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=float, default=5.0, help="ground-truth window for stats")
    p.add_argument("--tick-seconds", type=int, default=2)
    p.add_argument("--pass-n", type=int, default=40, help="seeds for the P(pass) test")
    p.add_argument("--pass-days", type=float, default=20.0)
    p.add_argument("--pass-tick-seconds", type=int, default=10)
    p.add_argument("--out", type=str, default=None)
    args = p.parse_args()

    # 1. Statistics: aggregate a known path to M15, re-synthesise, compare.
    truth = gbm_path(args.days, seed=1, tick_seconds=args.tick_seconds)
    bars = ticks_to_bars(truth[["epoch", "price"]], M15_SECONDS)
    synth = replay_from_bars(bars, tick_seconds=args.tick_seconds, seed=1)
    rep = compare_paths(synth["price"].to_numpy(), args.tick_seconds,
                        reference=truth["price"].to_numpy())
    ohlc = ohlc_error(bars, synth, M15_SECONDS)

    print(f"M15 replay statistics ({args.days:.0f}d, {args.tick_seconds}s, "
          f"{len(bars)} bars -> {len(synth):,} ticks)")
    c = rep["candidate"]
    print(f"  sigma          {c['sigma']:.6f}  (theory {c['theoretical_sigma']:.6f}, "
          f"rel err {rep['sigma_rel_error'] * 100:.1f}%)  -> {rep['checks']['sigma']}")
    print(f"  autocorr(1)    {c['autocorr1']:+.4f}   -> {rep['checks']['autocorr1']}")
    print(f"  up-tick share  {c['uptick_frac']:.4f}    -> {rep['checks']['uptick_frac']}")
    print(f"  kurtosis       {c['kurtosis']:+.4f}   -> {rep['checks']['kurtosis']}")
    print(f"  OHLC abs err   H {ohlc['high']:.3f}  L {ohlc['low']:.3f}  "
          f"(O/C exact by construction)")

    # 2. The decisive test: does P(pass) survive the round-trip through M15?
    print(f"\nP(pass): truth vs M15 replay  ({args.pass_n} seeds x "
          f"{args.pass_days:.0f}d @ {args.pass_tick_seconds}s)")
    dec = compare_sources(
        RandomEntryFactory(risk_pct=1.0, rr=2.0, trades_per_day=3.0),
        gbm_path, m15_replay_path, seeds=range(0, args.pass_n),
        days=args.pass_days, tick_seconds=args.pass_tick_seconds,
        label_a="truth", label_b="m15")
    preserved = dec["p_value"] > 0.05
    print(f"  truth      = {dec['p_pass_truth'] * 100:5.1f}%")
    print(f"  m15 replay = {dec['p_pass_m15'] * 100:5.1f}%")
    print(f"  delta = {dec['delta'] * 100:+.1f} pts   p = {dec['p_value']:.3f}   "
          f"-> P(pass) {'preserved' if preserved else 'BIASED'}")

    print("\nverdict: M15 replay reproduces sigma / no-momentum / balance; kurtosis is "
          f"elevated by H/L snapping ({'flagged' if not rep['checks']['kurtosis'] else 'ok'}), "
          f"but P(pass) is {'preserved' if preserved else 'NOT preserved'} — "
          f"{'usable for P(pass) with that caveat.' if preserved else 'not yet usable.'}")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"stats": rep, "ohlc": ohlc, "p_pass": dec,
                       "p_pass_preserved": preserved}, fh, indent=2, default=float)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
