"""Preliminary Boom/Crash hazard study (spec section 12)."""
from __future__ import annotations

import sys

import numpy as np

from propfirm.data import store
from propfirm.data.symbols import load_registry
from propfirm.research.spikes import detect, hazard_table


def main() -> int:
    reg = load_registry()
    for alias in ["boom1000", "crash1000"]:
        sym = reg[alias].symbol
        try:
            df = store.read(store.tick_path(sym))
        except FileNotFoundError:
            print(f"{sym}: no tick data"); continue

        s = detect(df, sym)
        hrs = (df.epoch.max() - df.epoch.min()) / 3600
        print(f"\n{'='*66}\n{sym}   {s.n_ticks:,} ticks over {hrs:.1f}h\n{'='*66}")
        print(f"  spikes detected      {s.n_spikes}")
        print(f"  mean interval        {s.mean_interval:,.1f} ticks   (spec says ~1000)")
        print(f"  median interval      {s.median_interval:,.1f}")
        print(f"  std interval         {s.std_interval:,.1f}")
        print(f"  range                {s.min_interval} .. {s.max_interval}")
        print(f"  mean spike size      {s.mean_spike_pct:+.4f}%")
        print(f"  mean drift/tick      {s.mean_drift_pct:+.6f}%")
        print(f"  drift over interval  {s.drift_per_interval_pct:+.4f}%"
              f"   vs spike {s.mean_spike_raw_pct:+.4f}% (raw signed)")
        net = s.net_per_cycle_pct
        print(f"  NET per cycle        {net:+.4f}%   "
              f"({'~zero by design' if abs(net) < 0.05 else 'ASYMMETRIC'})")

        print(f"\n  COEFFICIENT OF VARIATION = {s.cv:.3f}")
        if np.isnan(s.cv):
            verdict = "insufficient data"
        elif s.cv > 1.15:
            verdict = "CLUSTERED - spikes bunch; hazard falls with age"
        elif s.cv < 0.85:
            verdict = "REGULAR - hazard RISES with age; waiting has edge"
        else:
            verdict = "consistent with MEMORYLESS (Poisson) - tick-counting worthless"
        print(f"    exponential/memoryless would give 1.000  ->  {verdict}")

        print(f"\n  empirical hazard by age:")
        h = hazard_table(s.intervals)
        print(f"    {'age (ticks)':>16}{'at risk':>10}{'events':>8}{'hazard':>10}")
        for _, r in h.iterrows():
            if r.at_risk == 0: continue
            print(f"    {int(r.age_from):>7}-{int(r.age_to):<8}{int(r.at_risk):>10}"
                  f"{int(r.events):>8}{r.hazard:>10.3f}")

        print(f"\n  NOTE: {s.n_spikes} spikes is UNDERPOWERED for a confident fit."
              f"\n  Need ~1000+; at ~86 spikes/day that is ~12 days of recording.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
