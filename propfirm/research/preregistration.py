"""Pre-registration and researcher calibration (BUILD_PROMPT §9.1).

The second overfitting defence: the predicted effect and an explicit falsification
criterion are recorded *before* the test is run. Registering the prediction up front
does two things — it stops a null result being re-narrated as a success after the
fact, and it yields a **calibration score** for whoever is proposing the ideas
(Opus-as-researcher), tracked from day one: how often do its predictions come true,
and are its confidences honest?

Each hypothesis also records the index **price level** at registration, because on
Vol75 spread cost is a fraction of a price that is itself a random walk (§6), so a
rule adopted at 50,000 must be re-checked at 28,000.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Hypothesis:
    id: str
    description: str
    metric: str                       # what the test measures, e.g. "p_pass_delta"
    predicted_effect: float           # predicted signed magnitude of the metric
    falsification: str                # the explicit criterion that would refute it
    price_level: float                # index price at registration (§6 regime note)
    confidence: float | None = None   # optional 0..1, for calibration scoring
    registered_at: float = field(default_factory=time.time)
    # --- resolution (set once, after the test) ---
    realized_effect: float | None = None
    outcome: str | None = None        # "confirmed" | "falsified" | "inconclusive"
    resolved_at: float | None = None

    @property
    def resolved(self) -> bool:
        return self.outcome is not None


def directional_confirmed(predicted: float, realized: float,
                          min_fraction: float = 0.5) -> bool:
    """Default criterion: same sign and at least `min_fraction` of the magnitude."""
    if predicted == 0:
        return abs(realized) <= 1e-12          # predicted "no effect"
    same_sign = (predicted > 0) == (realized > 0)
    return same_sign and abs(realized) >= min_fraction * abs(predicted)


@dataclass
class PreRegistry:
    """Append-only store of hypotheses, registered before testing and resolved after."""
    hypotheses: dict = field(default_factory=dict)

    def register(self, hyp: Hypothesis) -> Hypothesis:
        if hyp.id in self.hypotheses:
            raise ValueError(f"hypothesis {hyp.id!r} already registered")
        if hyp.realized_effect is not None or hyp.outcome is not None:
            raise ValueError("cannot register a hypothesis that is already resolved")
        self.hypotheses[hyp.id] = hyp
        return hyp

    def resolve(self, hyp_id: str, realized_effect: float,
                confirmed: bool | None = None, min_fraction: float = 0.5) -> Hypothesis:
        """Record the outcome. `confirmed` applies a caller-judged criterion;
        if None, the default directional rule is used."""
        hyp = self.hypotheses[hyp_id]
        if hyp.resolved:
            raise ValueError(f"hypothesis {hyp_id!r} already resolved")
        if confirmed is None:
            confirmed = directional_confirmed(hyp.predicted_effect, realized_effect,
                                              min_fraction)
        hyp.realized_effect = realized_effect
        hyp.outcome = "confirmed" if confirmed else "falsified"
        hyp.resolved_at = time.time()
        return hyp

    # --- calibration ---------------------------------------------------------

    def calibration(self) -> dict:
        """How well the researcher's predictions and confidences hold up.

        hit_rate: fraction of resolved hypotheses that were confirmed.
        brier: mean squared error of confidence vs outcome (lower is better), over
               resolved hypotheses that carried a confidence. Undefined ⇒ None.
        """
        resolved = [h for h in self.hypotheses.values() if h.resolved]
        n = len(resolved)
        if n == 0:
            return {"resolved": 0, "hit_rate": None, "brier": None}
        hits = sum(1 for h in resolved if h.outcome == "confirmed")
        scored = [h for h in resolved if h.confidence is not None]
        brier = (sum((h.confidence - (1.0 if h.outcome == "confirmed" else 0.0)) ** 2
                     for h in scored) / len(scored)) if scored else None
        return {"resolved": n, "hit_rate": hits / n,
                "brier": brier, "confirmed": hits}

    def write_jsonl(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            for h in self.hypotheses.values():
                fh.write(json.dumps(asdict(h)) + "\n")
