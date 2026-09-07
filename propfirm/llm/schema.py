"""Opus response schema and validation (BUILD_PROMPT §3).

The model's output is strict JSON, schema-validated. Anything malformed is not a
judgement call the simulator honours — it is a **no trade**, logged as a failure
(§3). That rule is what keeps a hallucinated or truncated response from silently
reaching a headline result.

`DECISION_JSON_SCHEMA` is fed to the Messages API as `output_config.format` so the
model is constrained at generation time; `parse_decision` re-validates on our side
because a constrained decode is still not a guarantee.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

ACTIONS = ("trade", "no_trade")

# Constrains the model at generation time (output_config.format). Structured
# outputs reject numeric min/max, so ranges are re-checked in parse_decision.
DECISION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "risk_pct": {"type": ["number", "null"]},
        "sl": {"type": ["number", "null"]},
        "tp": {"type": ["number", "null"]},
        "confidence": {"type": ["number", "null"]},
        "reasoning": {"type": "string"},
    },
    "required": ["action", "risk_pct", "sl", "tp", "confidence", "reasoning"],
    "additionalProperties": False,
}


@dataclass
class Decision:
    action: str = "no_trade"           # "trade" | "no_trade"
    risk_pct: float | None = None      # optional override; clamped to the core's risk
    sl: float | None = None            # optional stop override
    tp: float | None = None            # optional target override
    confidence: float | None = None    # 0..1, informational
    reasoning: str = ""
    malformed: bool = False            # set when the raw response failed validation

    @property
    def is_trade(self) -> bool:
        return self.action == "trade" and not self.malformed


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def parse_decision(raw: str | dict) -> Decision:
    """Validate a raw model response. Any failure ⇒ a logged no-trade Decision.

    Never raises: a malformed response must degrade to 'no trade', not crash the
    run and not be honoured as a decision.
    """
    try:
        data = raw if isinstance(raw, dict) else json.loads(raw)
    except (ValueError, TypeError):
        return Decision(malformed=True, reasoning="unparseable response")
    if not isinstance(data, dict):
        return Decision(malformed=True, reasoning="response was not an object")

    action = data.get("action")
    if action not in ACTIONS:
        return Decision(malformed=True, reasoning=f"bad action {action!r}")

    conf = _num(data.get("confidence"))
    if conf is not None:
        conf = max(0.0, min(1.0, conf))

    reasoning = data.get("reasoning")
    return Decision(
        action=action,
        risk_pct=_num(data.get("risk_pct")),
        sl=_num(data.get("sl")),
        tp=_num(data.get("tp")),
        confidence=conf,
        reasoning=reasoning if isinstance(reasoning, str) else "",
    )
