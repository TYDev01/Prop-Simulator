"""Spike detection and hazard analysis for Boom/Crash indices.

Spec section 12: the headline question is the shape of the hazard function

    P(spike on next tick | N ticks since last spike)

If spikes are Poisson (memoryless) the hazard is flat, tick-counting is worthless,
and the question is closed. If the inter-arrival distribution is anything else,
waiting N ticks carries genuine computable edge -- which would reorder the roadmap.

Detection is unambiguous in practice: measured on 24h of BOOM1000, drift ticks move
~0.0001% while spikes move ~0.3%, a separation of roughly three orders of magnitude.
Any threshold in the wide gap between them yields identical spike sets.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Spike direction by instrument family: Boom spikes up, Crash spikes down.
SPIKE_DIRECTION = {"BOOM": +1, "CRASH": -1}

# Threshold as a multiple of the median absolute drift tick. The drift/spike gap
# spans ~3 orders of magnitude, so this is deliberately loose.
SPIKE_THRESHOLD_MULT = 20.0


def spike_direction(symbol: str) -> int:
    for prefix, d in SPIKE_DIRECTION.items():
        if symbol.upper().startswith(prefix):
            return d
    raise ValueError(f"unknown spike direction for {symbol}")


@dataclass
class SpikeStats:
    symbol: str
    n_ticks: int
    n_spikes: int
    mean_interval: float
    median_interval: float
    std_interval: float
    cv: float                  # std/mean; 1.0 => memoryless (exponential)
    min_interval: int
    max_interval: int
    mean_spike_pct: float          # signed in the SPIKE direction (always positive)
    mean_spike_raw_pct: float      # signed in RAW price terms (down = negative)
    mean_drift_pct: float          # raw signed, per tick
    drift_per_interval_pct: float  # raw signed, accumulated over one mean interval
    net_per_cycle_pct: float       # raw signed: drift over interval + spike
    intervals: np.ndarray
    spike_sizes: np.ndarray


def detect(df: pd.DataFrame, symbol: str,
           threshold_mult: float = SPIKE_THRESHOLD_MULT) -> SpikeStats:
    """Detect spikes and summarise inter-arrival behaviour."""
    d = spike_direction(symbol)
    df = df.sort_values("epoch").reset_index(drop=True)
    price = df["price"].to_numpy(dtype=float)
    ret = np.diff(price) / price[:-1] * 100.0          # percent per tick

    signed = ret * d                                    # positive = spike direction
    drift = ret[signed <= 0]
    drift_scale = float(np.median(np.abs(drift))) if drift.size else 0.0
    threshold = max(drift_scale * threshold_mult, 1e-9)

    idx = np.flatnonzero(signed > threshold)            # spike tick indices
    intervals = np.diff(idx) if idx.size > 1 else np.array([], dtype=int)
    sizes = signed[idx] if idx.size else np.array([])

    mean_iv = float(intervals.mean()) if intervals.size else float("nan")
    std_iv = float(intervals.std(ddof=1)) if intervals.size > 1 else float("nan")

    # Spike sizes are measured in the spike direction, drift in raw price terms.
    # They must be put on the same sign convention before they can be summed.
    mean_spike = float(sizes.mean()) if sizes.size else float("nan")
    mean_spike_raw = mean_spike * d
    mean_drift = float(np.mean(drift)) if drift.size else float("nan")
    drift_per_iv = (mean_drift * mean_iv
                    if drift.size and intervals.size else float("nan"))

    return SpikeStats(
        symbol=symbol,
        n_ticks=len(price),
        n_spikes=int(idx.size),
        mean_interval=mean_iv,
        median_interval=float(np.median(intervals)) if intervals.size else float("nan"),
        std_interval=std_iv,
        cv=std_iv / mean_iv if intervals.size > 1 and mean_iv else float("nan"),
        min_interval=int(intervals.min()) if intervals.size else 0,
        max_interval=int(intervals.max()) if intervals.size else 0,
        mean_spike_pct=mean_spike,
        mean_spike_raw_pct=mean_spike_raw,
        mean_drift_pct=mean_drift,
        drift_per_interval_pct=drift_per_iv,
        net_per_cycle_pct=drift_per_iv + mean_spike_raw,
        intervals=intervals,
        spike_sizes=sizes,
    )


def hazard_table(intervals: np.ndarray, n_bins: int = 8) -> pd.DataFrame:
    """Empirical discrete hazard: P(spike now | survived to here), by age bucket.

    A flat hazard column is the memoryless signature. A rising one means waiting
    pays; a falling one means spikes cluster.
    """
    if intervals.size == 0:
        return pd.DataFrame()
    edges = np.linspace(0, intervals.max() + 1, n_bins + 1).astype(int)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        at_risk = int((intervals >= lo).sum())
        events = int(((intervals >= lo) & (intervals < hi)).sum())
        rows.append({
            "age_from": lo, "age_to": hi,
            "at_risk": at_risk, "events": events,
            "hazard": events / at_risk if at_risk else np.nan,
        })
    return pd.DataFrame(rows)
