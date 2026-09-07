"""Discovery / validation / holdout partitioning (BUILD_PROMPT §9.1).

On a zero-drift instrument a loop that keeps whatever "worked" on the trades it just
saw is an overfitting machine. The first hard defence is strict partitioning: ideas
are found on the discovery seeds, checked on the disjoint validation seeds, and the
holdout is touched **exactly once**, at the very end.

Seeds are the unit of the experiment (each seed is one independent GBM path), so the
partition is a split of the seed space into three non-overlapping ranges. The holdout
guard is stateful and enforced: a second access raises, so "just peeking" at the
holdout is a program error, not a matter of discipline.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class HoldoutViolation(RuntimeError):
    """Raised when the holdout partition is accessed more than once."""


@dataclass(frozen=True)
class Partition:
    """A contiguous seed-space split. Ranges are half-open [start, end)."""
    discovery: tuple[int, int]
    validation: tuple[int, int]
    holdout: tuple[int, int]

    @staticmethod
    def make(n: int, discovery_frac: float = 0.6, validation_frac: float = 0.2,
             seed0: int = 0) -> "Partition":
        """Split n seeds into discovery / validation / holdout by fraction."""
        d = int(n * discovery_frac)
        v = int(n * validation_frac)
        return Partition(
            discovery=(seed0, seed0 + d),
            validation=(seed0 + d, seed0 + d + v),
            holdout=(seed0 + d + v, seed0 + n),
        )

    def seeds(self, which: str) -> range:
        lo, hi = getattr(self, which)
        return range(lo, hi)

    def overlaps(self) -> bool:
        rs = [self.discovery, self.validation, self.holdout]
        rs.sort()
        return any(rs[i][1] > rs[i + 1][0] for i in range(len(rs) - 1))


@dataclass
class HoldoutGuard:
    """Enforces the touch-once rule on the holdout partition."""
    partition: Partition
    _spent: bool = field(default=False, init=False)

    @property
    def spent(self) -> bool:
        return self._spent

    def take(self) -> range:
        """Return the holdout seeds, consuming the single permitted access."""
        if self._spent:
            raise HoldoutViolation(
                "holdout already used once; a second access would invalidate it")
        self._spent = True
        return self.partition.seeds("holdout")
