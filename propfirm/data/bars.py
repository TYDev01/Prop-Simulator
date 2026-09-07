"""Aggregate a tick series into OHLC bars.

The inverse of tick synthesis: group ticks into fixed-width buckets and take the
open/high/low/close of each. Needed to build the M15 anchors the long-history replay
synthesises from, and to validate that replay by aggregating a known tick path and
re-expanding it.

Bar epoch is the bucket's start (`bucket * bar_seconds`), matching how the feed and
the strategies bucket time elsewhere.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ticks_to_bars(ticks: pd.DataFrame, bar_seconds: int) -> pd.DataFrame:
    """Aggregate [epoch, price] ticks into [epoch, open, high, low, close] bars."""
    if not {"epoch", "price"}.issubset(ticks.columns):
        raise ValueError("ticks needs columns [epoch, price]")
    df = ticks.sort_values("epoch").reset_index(drop=True)
    bucket = (df["epoch"].to_numpy(dtype=np.int64) // bar_seconds)
    g = df.assign(_b=bucket).groupby("_b")["price"]
    out = pd.DataFrame({
        "open": g.first(),
        "high": g.max(),
        "low": g.min(),
        "close": g.last(),
    })
    out.insert(0, "epoch", out.index.to_numpy(dtype=np.int64) * bar_seconds)
    return out.reset_index(drop=True)
