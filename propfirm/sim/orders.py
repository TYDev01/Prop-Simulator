"""Resting entry orders (BUILD_PROMPT §5).

Market entries open at the current price. Limit and stop entries rest until the
market reaches a trigger, and they fill with the same honesty the exit engine uses:
a limit fills at its level (no price improvement modelled), a stop fills at the worse
of its level and the price actually available, so a gap through a stop is charged the
gap. Trailing-stop and break-even parameters ride along on the order so the resulting
position is managed from its first tick.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OrderType(str, Enum):
    LIMIT = "limit"    # buy below / sell above the market — a patient entry
    STOP = "stop"      # buy above / sell below the market — a breakout entry


@dataclass
class PendingOrder:
    direction: int                 # +1 long, -1 short
    lots: float
    order_type: OrderType
    trigger: float                 # mid price at which the order activates
    sl: float | None = None
    tp: float | None = None
    tag: str = ""
    meta: dict = field(default_factory=dict)
    expiry_epoch: int | None = None    # cancelled if unfilled by this epoch (GTD)
    # Management to attach to the position once filled:
    trail_distance: float | None = None    # ratchet the stop this far behind price
    breakeven_trigger: float | None = None # move the stop to break-even at this price
    breakeven_offset: float = 0.0          # entry +/- this when the stop goes to BE

    def triggered(self, prev_mid: float, mid: float) -> bool:
        """Did this order's trigger get crossed over the [prev_mid, mid] step?"""
        lo, hi = min(prev_mid, mid), max(prev_mid, mid)
        if lo <= self.trigger <= hi:
            return True
        # A gap that leapt clean over the trigger without bracketing it.
        if self.order_type is OrderType.LIMIT:
            # Buy limit fills if price fell to/below it; sell limit if rose to/above.
            return mid <= self.trigger if self.direction > 0 else mid >= self.trigger
        # Buy stop fills if price rose to/above it; sell stop if fell to/below.
        return mid >= self.trigger if self.direction > 0 else mid <= self.trigger

    def fill_mid(self, mid: float) -> float:
        """The mid price the fill is struck at, before spread.

        Limit: its level (favourable resting order — no better). Stop: the worse of
        the level and the available price, so a gap is charged, not absorbed.
        """
        if self.order_type is OrderType.LIMIT:
            return self.trigger
        if self.direction > 0:                 # buy stop: worse = higher
            return max(self.trigger, mid)
        return min(self.trigger, mid)          # sell stop: worse = lower
