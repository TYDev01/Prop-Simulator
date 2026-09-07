"""Leakage gate as a test (REMAINING.md §5.1), lifted from scripts/test_leakage.py.

A backtest that lets the strategy see one tick past the cursor produces results
that are not merely optimistic but meaningless, and it fails silently. So this is
the one property the suite must never let regress.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from propfirm.sim.feed import LookaheadError, TickFeed

SENTINEL = 1.0e12


def _random_walk(n: int = 3_000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    real = 50_000 + np.cumsum(rng.normal(0, 5, n))
    return pd.DataFrame({"epoch": np.arange(n), "price": real})


def test_sentinel_never_leaks_past_cursor():
    df = _random_walk()
    real = df["price"].to_numpy()
    n = len(real)
    inspected = 0
    for cut in range(1, n, 97):
        poisoned = real.copy()
        poisoned[cut:] = SENTINEL
        feed = TickFeed(pd.DataFrame({"epoch": df.epoch, "price": poisoned}))
        for _ in range(cut):
            view = feed.step()
            if view is None:
                break
            inspected += 1
            assert view.price < SENTINEL
            assert not (view.history >= SENTINEL).any()
    assert inspected > 0


def test_history_ends_exactly_at_cursor():
    feed = TickFeed(_random_walk())
    while True:
        view = feed.step()
        if view is None:
            break
        assert view.history[-1] == view.price
        assert len(view.history) == min(feed.position + 1, 20_000)


def test_view_history_is_immutable():
    feed = TickFeed(_random_walk())
    view = feed.step()
    with pytest.raises(ValueError):
        view.history[0] = 123.0


def test_peek_future_is_explicit_and_correct():
    df = _random_walk()
    feed = TickFeed(df)
    feed.step()
    assert feed.peek_future() == df["price"].to_numpy()[1]


def test_peek_future_raises_when_exhausted():
    feed = TickFeed(_random_walk(n=3))
    for _ in range(3):
        feed.step()
    with pytest.raises(LookaheadError):
        feed.peek_future()
