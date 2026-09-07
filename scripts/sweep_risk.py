"""First real measurement: P(pass) for a zero-edge strategy under strict rules.

Spec section 10 makes P(pass) the objective, and section 2.3 says the only real
degrees of freedom on a driftless instrument are risk per trade, trade frequency,
and how they interact with the drawdown limits. So the first experiment worth
running is a sweep over exactly those, using random entry -- because if entries
carry no information, this *is* the strategy.
"""
from __future__ import annotations

import json
import sys
import time

from propfirm.config import DOCS
from propfirm.research.montecarlo import run_trials, summarise
from propfirm.strategy.controls import RandomEntryFactory

N_TRIALS = int(sys.argv[1]) if len(sys.argv) > 1 else 100
GRID = [
    dict(risk_pct=r, rr=rr, trades_per_day=tpd)
    for r in (0.5, 1.0, 2.0, 3.0)
    for rr in (2.0,)
    for tpd in (3.0,)
] + [
    dict(risk_pct=1.0, rr=rr, trades_per_day=3.0) for rr in (1.0, 3.0)
] + [
    dict(risk_pct=1.0, rr=2.0, trades_per_day=tpd) for tpd in (1.0, 8.0)
]


def main() -> int:
    rows = []
    print(f"random entry, strict-100k, {N_TRIALS} trials per config, 30d, 2s ticks\n")
    print(f"  {'risk%':>6}{'RR':>5}{'tr/day':>7}{'P(pass)':>9}{'daily':>7}"
          f"{'total':>7}{'exp':>5}{'med eq':>10}{'trades':>8}{'blocked':>8}{'sec':>7}")
    for cfg in GRID:
        t0 = time.time()
        res = run_trials(RandomEntryFactory(**cfg), n=N_TRIALS, days=30.0)
        s = summarise(res, label=json.dumps(cfg))
        s.update(cfg)
        rows.append(s)
        print(f"  {cfg['risk_pct']:>6.1f}{cfg['rr']:>5.1f}{cfg['trades_per_day']:>7.1f}"
              f"{s['p_pass']*100:>8.1f}%{s['breached_daily']:>7}{s['breached_total']:>7}"
              f"{s['expired']:>5}{s['median_equity']:>10,.0f}{s['mean_trades']:>8.1f}"
              f"{s['blocked_at_target']:>8}{time.time()-t0:>7.0f}")

    out = DOCS / "sweep_risk.json"
    out.write_text(json.dumps(rows, indent=2))
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
