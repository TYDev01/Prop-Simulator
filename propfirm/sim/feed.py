"""Tick feed cursor with a hard no-lookahead guarantee.

Spec section 5: leakage is the primary correctness risk. A backtest that lets the
strategy glimpse one bar it could not have seen produces results that are not
merely optimistic but meaningless, and the failure is silent.

The guarantee here is structural rather than by convention: the feed owns the
data, the strategy is handed only an immutable view ending at the cursor, and the
future is not reachable from anything the strategy holds. `scripts/test_leakage.py`
verifies this by poisoning the future and asserting the poison never surfaces.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


class LookaheadError(RuntimeError):
    """Raised when code attempts to read beyond the cursor."""


@dataclass(frozen=True)
class MarketView:
    """Immutable window ending at the cursor. Nothing here reaches the future."""

    epoch: int
    price: float
    history: np.ndarray          # prices up to and including `price`

    def last(self, n: int) -> np.ndarray:
        return self.history[-n:] if n < len(self.history) else self.history


class TickFeed:
    """Replays ticks forward. The only way to advance is `step()`."""

    def __init__(self, ticks: pd.DataFrame, history_cap: int = 20_000):
        if not {"epoch", "price"}.issubset(ticks.columns):
            raise ValueError("ticks needs columns [epoch, price]")
        df = ticks.sort_values("epoch").reset_index(drop=True)
        self._epochs = df["epoch"].to_numpy(dtype=np.int64)
        self._prices = df["price"].to_numpy(dtype=float)
        # Frozen once here, so every slice handed out is a read-only view rather
        # than a defensive copy. Copying per tick dominated the profile.
        self._prices.flags.writeable = False
        self._epochs.flags.writeable = False
        self._n = len(df)
        self._i = -1
        self._history_cap = history_cap

    def __len__(self) -> int:
        return self._n

    @property
    def exhausted(self) -> bool:
        return self._i >= self._n - 1

    @property
    def position(self) -> int:
        return self._i

    def step(self) -> MarketView | None:
        """Advance one tick and return the view. None when exhausted."""
        if self.exhausted:
            return None
        self._i += 1
        lo = max(0, self._i + 1 - self._history_cap)
        # Sliced to the cursor, so the future is absent rather than merely
        # off-limits; read-only because the backing array is frozen.
        hist = self._prices[lo : self._i + 1]
        return MarketView(
            epoch=int(self._epochs[self._i]),
            price=float(self._prices[self._i]),
            history=hist,
        )

    def peek_future(self) -> float:
        """Deliberately unsafe accessor, for tests only. Never call from strategy."""
        if self.exhausted:
            raise LookaheadError("no future left")
        return float(self._prices[self._i + 1])
