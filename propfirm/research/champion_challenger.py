"""Champion / challenger with versioned promotion (BUILD_PROMPT §8, §9.1).

Never mutate the live strategy. The champion trades; challengers run in parallel on
identical data; a challenger is promoted only if it beats the champion **out of
sample** by a stated margin. Every promotion is a new version carrying its rationale,
the evidence, and the test result — the audit trail of how and why the strategy
changed.

This module owns the *bookkeeping and the promotion rule*; scoring is injected, so
the caller runs each candidate on the correct partition (validation to choose,
holdout once to confirm — see propfirm/research/partition.py) and hands back a score.
Higher is better; the objective is P(pass), per §10.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class StrategyVersion:
    version: int
    label: str
    factory: object                    # a picklable strategy factory
    rationale: str = ""
    evidence: dict = field(default_factory=dict)
    parent: int | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class ChampionChallenger:
    """Holds the reigning champion and the rule + history for replacing it."""
    champion: StrategyVersion
    promotion_margin: float = 0.02     # required OOS edge over the champion (abs metric)
    history: list = field(default_factory=list)
    _next_version: int = field(default=1, init=False)

    def __post_init__(self) -> None:
        if not self.history:
            self.history = [self.champion]
        self._next_version = max(v.version for v in self.history) + 1

    def new_version(self, label: str, factory: object, rationale: str = "",
                    evidence: dict | None = None) -> StrategyVersion:
        """Mint a challenger version (not yet promoted)."""
        v = StrategyVersion(version=self._next_version, label=label, factory=factory,
                            rationale=rationale, evidence=evidence or {},
                            parent=self.champion.version)
        self._next_version += 1
        return v

    def should_promote(self, champion_score: float, challenger_score: float) -> bool:
        """Promotion requires beating the champion by the full margin."""
        return challenger_score >= champion_score + self.promotion_margin

    def consider(self, challenger: StrategyVersion, score_fn: Callable[[object], float],
                 seeds: range) -> dict:
        """Score champion and challenger on the same out-of-sample seeds, then decide.

        `score_fn(factory)` must evaluate a factory over `seeds` and return a
        higher-is-better metric. Promotes the challenger iff it clears the margin;
        records the decision as evidence on the version either way.
        """
        champ_score = score_fn(self.champion.factory)
        chall_score = score_fn(challenger.factory)
        promote = self.should_promote(champ_score, chall_score)
        decision = {
            "champion_version": self.champion.version,
            "champion_score": champ_score,
            "challenger_score": chall_score,
            "margin_required": self.promotion_margin,
            "seeds": [seeds.start, seeds.stop],
            "promoted": promote,
        }
        challenger.evidence = {**challenger.evidence, "promotion_test": decision}
        if promote:
            self.history.append(challenger)
            self.champion = challenger
        return decision
