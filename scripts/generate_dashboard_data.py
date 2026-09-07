"""Generate the results bundle the dashboard renders (docs/dashboard_data.json).

Runs the live pipeline end to end — the strategy A/B (random vs seed v1 vs v2), the
champion/challenger promotion decision on out-of-sample seeds, a risk sweep, and two
contrasting equity curves — and folds in the campaign and M15-replay findings. This
is the data the HTML dashboard reads; nothing there is hand-typed.

Usage:
    PYTHONPATH=. python scripts/generate_dashboard_data.py [--n N] [--days D]
        [--tick-seconds S] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from propfirm.research.champion_challenger import ChampionChallenger, StrategyVersion
from propfirm.research.evaluate import p_pass_over
from propfirm.research.montecarlo import gbm_path, run_trials, summarise
from propfirm.research.partition import Partition
from propfirm.research.trade_log import records_from_ledger
from propfirm.research.trade_log import summarise as summarise_trades
from propfirm.rules.ruleset import STRICT_100K
from propfirm.sim.contract import VOL75
from propfirm.sim.engine import SimEngine
from propfirm.strategy.controls import RandomEntryFactory
from propfirm.strategy.seed_mae import ReducedMAEFactory, seed_v2_factory

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def _strategy_row(name: str, factory, seeds, ev) -> dict:
    s = summarise(run_trials(factory, n=len(seeds), seed0=seeds.start, **ev))
    return {"name": name, "p_pass": s["p_pass"], "n_pass": s["n_pass"],
            "breached": s["breached_daily"] + s["breached_total"],
            "expired": s["expired"], "mean_trades": round(s["mean_trades"], 1),
            "median_equity": round(s["median_equity"], 0)}


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=60)
    p.add_argument("--days", type=float, default=30.0)
    p.add_argument("--tick-seconds", type=int, default=10)
    p.add_argument("--out", type=str, default=str(DOCS / "dashboard_data.json"))
    args = p.parse_args()
    ev = dict(days=args.days, tick_seconds=args.tick_seconds)
    t0 = time.time()

    part = Partition.make(args.n, discovery_frac=0.6, validation_frac=0.2)
    disc, val = part.seeds("discovery"), part.seeds("validation")

    rand = RandomEntryFactory(risk_pct=1.0, rr=2.0, trades_per_day=3.0)
    v1 = ReducedMAEFactory(risk_pct=1.0, rr=2.0)
    v2 = seed_v2_factory()

    print("strategy A/B on discovery seeds ...", flush=True)
    strategies = [
        _strategy_row("random-entry", rand, disc, ev),
        _strategy_row("seed-v1", v1, disc, ev),
        _strategy_row("seed-v2", v2, disc, ev),
    ]

    print("champion/challenger on validation seeds ...", flush=True)
    champ = StrategyVersion(version=1, label="seed-v1", factory=v1,
                            rationale="structural stops + selectivity")
    cc = ChampionChallenger(champion=champ, promotion_margin=0.03)
    challenger = cc.new_version("seed-v2", v2,
                                rationale="looser channel + break-even/partial/trail")
    decision = cc.consider(challenger, score_fn=lambda f: p_pass_over(f, val, **ev),
                           seeds=val)

    print("risk sweep ...", flush=True)
    risk_sweep = []
    for risk in (0.5, 1.0, 2.0, 3.0):
        s = summarise(run_trials(RandomEntryFactory(risk_pct=risk, rr=2.0,
                                                    trades_per_day=3.0),
                                 n=args.n, seed0=0, **ev))
        risk_sweep.append({"risk_pct": risk, "p_pass": s["p_pass"]})

    print("equity curves ...", flush=True)
    eng = SimEngine(spec=VOL75, rules=STRICT_100K)
    curves, sample_trades = {}, []
    for seed in range(0, 30):
        r = eng.run(gbm_path(args.days, seed, tick_seconds=args.tick_seconds),
                    v2(seed), sample_every=200)
        key = "pass" if r.outcome.value == "passed" else (
              "breach" if "breached" in r.outcome.value else "expire")
        if key not in curves and r.equity_samples:
            t0e = r.equity_samples[0][0]
            curves[key] = {"outcome": r.outcome.value,
                           "points": [{"d": round((e - t0e) / 86400.0, 2),
                                       "eq": round(q, 0)} for e, q in r.equity_samples]}
            if not sample_trades:
                sample_trades = [{"pnl": round(t.pnl, 1), "reason": t.reason,
                                  "r": round(t.r_multiple, 2), "dir": t.direction}
                                 for t in records_from_ledger(r.ledger)[:10]]
        if {"pass", "breach", "expire"}.issubset(curves):
            break

    trade_summary = None
    for seed in range(0, 20):
        r = eng.run(gbm_path(args.days, seed, tick_seconds=args.tick_seconds), v2(seed))
        recs = records_from_ledger(r.ledger)
        if recs:
            trade_summary = {k: round(v, 3) for k, v in summarise_trades(recs).items()}
            break

    bundle = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "config": {"n": args.n, "days": args.days, "tick_seconds": args.tick_seconds,
                   "ruleset": STRICT_100K.name, "instrument": VOL75.display_name},
        "strategies": strategies,
        "champion_decision": decision,
        "risk_sweep": risk_sweep,
        "equity_curves": curves,
        "trade_summary": trade_summary,
        "sample_trades": sample_trades,
        "campaign": _load(DOCS / "campaign_strict.json"),
        "m15_validation": _load(DOCS / "m15_replay_validation.json"),
        "elapsed_s": round(time.time() - t0, 1),
    }
    Path(args.out).write_text(json.dumps(bundle, indent=2, default=float))
    print(f"\nwrote {args.out}  ({bundle['elapsed_s']}s)")
    print(f"  strategies: " + ", ".join(f"{s['name']} {s['p_pass']*100:.0f}%"
                                        for s in strategies))
    print(f"  promotion (v2 over v1): {decision['promoted']} "
          f"(v1 {decision['champion_score']*100:.0f}% vs v2 "
          f"{decision['challenger_score']*100:.0f}%)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
