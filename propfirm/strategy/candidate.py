"""A proposed trade, before it is committed.

The candidate is the seam between the deterministic core and the Opus overlay
(BUILD_PROMPT §3). The core *detects* a candidate from the data; the overlay is
consulted only then, never on the tick path. Keeping the candidate a plain value
means core-only and core+overlay runs share identical detection logic — the §3
dual-mode requirement — and differ solely in whether a decision hook gates it.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Candidate:
    direction: int                # +1 long, -1 short
    lots: float
    entry: float                  # expected fill (mid + half-spread), for reference
    sl: float
    tp: float
    stop_dist: float              # price distance to the stop, for re-sizing
    invalidation: float           # the swing level; the declared reason to be wrong
    risk_pct: float               # the risk the core sized this at
    features: dict = field(default_factory=dict)   # precomputed, for the state packet
    tag: str = "mae"
