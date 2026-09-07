"""Long-history replay from M15 anchors (BUILD_PROMPT §2.7, §5).

Deriv serves only 24h of ticks and ~6 weeks of M1, but a full year of M15 candles is
available. Replay expands those M15 bars into a tick series with the same
bridge-conditioned synthesis used for M1 (propfirm/data/synth_ticks.py) — legitimate
here because Vol75's generating process is published (driftless GBM at 75% vol), so a
Brownian bridge between known OHLC anchors reproduces the true process rather than
guessing at one.

The catch this module has to answer for is resolution: an M15 bar pins four points
over 900 seconds, far coarser than M1's four points over 60. Whether that coarseness
distorts what the simulator measures is validated in scripts/validate_m15_replay.py;
this module provides the pipeline and the picklable path source that validation and
the Monte Carlo harness plug in.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from propfirm.data.bars import ticks_to_bars
from propfirm.data.synth_ticks import synth_ticks
from propfirm.research.montecarlo import gbm_path

M15_SECONDS = 900
# Fewer bridge candidates than the M1 path (128): the extremes matter less across a
# year of Monte Carlo than throughput does. Raise it for a faithful single replay.
REPLAY_CANDIDATES = 24


def replay_from_bars(bars: pd.DataFrame, tick_seconds: int = 2, seed: int = 0,
                     bar_seconds: int = M15_SECONDS,
                     n_candidates: int = REPLAY_CANDIDATES,
                     chunk_bars: int = 2000) -> pd.DataFrame:
    """Expand OHLC bars into a synthetic tick series, in bar-chunks to bound memory.

    A year of M15 is ~35k bars ⇒ ~15.7M ticks at 2s; synthesising in chunks keeps the
    working set small. Chunks are seeded off `seed` so the whole replay is reproducible.
    """
    bars = bars.sort_values("epoch").reset_index(drop=True)
    frames = []
    for start in range(0, len(bars), chunk_bars):
        chunk = bars.iloc[start:start + chunk_bars]
        frames.append(synth_ticks(chunk, tick_seconds, seed=seed + start,
                                  bar_seconds=bar_seconds, n_candidates=n_candidates))
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates(subset=["epoch"]).sort_values("epoch").reset_index(drop=True)


def m15_replay_path(days: float, seed: int, tick_seconds: int = 2) -> pd.DataFrame:
    """A path source that routes GBM through the M15 replay pipeline.

    Generates the true tick path, aggregates it to M15 bars, and re-synthesises ticks
    from those bars — exactly the loss of information a real M15 replay incurs. Drop
    this into run_trials/compare_sources against `gbm_path` to test whether replaying
    from M15 changes what the simulator concludes. Module-level so it pickles to the
    Monte Carlo workers.
    """
    truth = gbm_path(days, seed, tick_seconds=tick_seconds)
    bars = ticks_to_bars(truth[["epoch", "price"]], M15_SECONDS)
    ticks = replay_from_bars(bars, tick_seconds=tick_seconds, seed=seed)
    # Re-base epochs onto the truth's clock so the engine's day boundaries line up.
    ticks = ticks.copy()
    ticks["epoch"] = ticks["epoch"].to_numpy(dtype=np.int64) - int(ticks["epoch"].iloc[0]) \
        + int(truth["epoch"].iloc[0])
    return ticks[["epoch", "price"]]
