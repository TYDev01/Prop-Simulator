"""Running multiple-testing ledger (BUILD_PROMPT §9.1).

The third overfitting defence, and the one most easily skipped: **a ledger that
never resets**. The best of 50 tested ideas needs a far higher bar than the best of
3, so every test ever run counts against the significance threshold for the next
one. On a zero-edge instrument this is what stops the loop from eventually finding a
"winner" that is only the tail of many coin flips.

Two corrections are offered. Bonferroni (α / n) is the blunt, always-available bar
— it needs only a count. Benjamini-Hochberg controls the false-discovery rate when
p-values are recorded. The ledger is append-only and persistable; loading it back
restores the full history so the count keeps growing across sessions.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class TestEntry:
    hypothesis_id: str
    metric: str
    effect: float
    p_value: float | None = None
    n: int | None = None
    price_level: float | None = None


@dataclass
class MultipleTestingLedger:
    """Append-only record of every test run. Never reset it."""
    entries: list = field(default_factory=list)

    def record(self, hypothesis_id: str, metric: str, effect: float,
               p_value: float | None = None, n: int | None = None,
               price_level: float | None = None) -> TestEntry:
        e = TestEntry(hypothesis_id, metric, effect, p_value, n, price_level)
        self.entries.append(e)
        return e

    @property
    def n_tests(self) -> int:
        return len(self.entries)

    def bonferroni_bar(self, alpha: float = 0.05) -> float:
        """The corrected per-test significance threshold given how many have run."""
        return alpha / self.n_tests if self.n_tests else alpha

    def survives_bonferroni(self, p_value: float, alpha: float = 0.05) -> bool:
        return p_value <= self.bonferroni_bar(alpha)

    def bh_threshold(self, alpha: float = 0.05) -> float | None:
        """Benjamini-Hochberg FDR threshold across all recorded p-values.

        Returns the largest p-value that passes the BH step-up, or None if no test
        survives (or no p-values are recorded).
        """
        ps = sorted(e.p_value for e in self.entries if e.p_value is not None)
        m = len(ps)
        if m == 0:
            return None
        passing = None
        for i, p in enumerate(ps, start=1):
            if p <= alpha * i / m:
                passing = p                    # step-up: keep the largest that passes
        return passing

    def discoveries(self, alpha: float = 0.05) -> list[TestEntry]:
        """Entries whose p-value survives the BH threshold across all tests."""
        thresh = self.bh_threshold(alpha)
        if thresh is None:
            return []
        return [e for e in self.entries if e.p_value is not None and e.p_value <= thresh]

    def write_jsonl(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            for e in self.entries:
                fh.write(json.dumps(asdict(e)) + "\n")

    @classmethod
    def load_jsonl(cls, path: str) -> "MultipleTestingLedger":
        """Restore the full history so the never-reset count survives a restart."""
        led = cls()
        p = Path(path)
        if not p.exists():
            return led
        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    led.entries.append(TestEntry(**json.loads(line)))
        return led
