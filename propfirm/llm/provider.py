"""Decision providers (BUILD_PROMPT §3, Layer 2).

A provider turns a state packet into a `Decision`. Two implementations:

  MockProvider  A deterministic policy function, no network, no cost. This is what
                the test suite and the offline dual-mode A/B run against, so the
                whole overlay is exercisable without spending a cent.

  OpusProvider  The real Claude client, with strict-JSON output, per-call cost
                accounting, and full prompt/response logging (§14: "that log is the
                research dataset"). The anthropic SDK is imported lazily, so nothing
                here forces the dependency on the simulator or the tests.

Both satisfy the same `DecisionProvider` protocol, so the overlay never knows which
one it holds — the seam that makes the core-vs-core+overlay comparison honest.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from propfirm.llm.schema import DECISION_JSON_SCHEMA, Decision, parse_decision

# Claude Opus 5 list price, USD per input/output token (docs: $5 / $25 per 1M).
OPUS_INPUT_PER_TOKEN = 5.0 / 1_000_000
OPUS_OUTPUT_PER_TOKEN = 25.0 / 1_000_000
DEFAULT_MODEL = "claude-opus-5"

SYSTEM_PROMPT = (
    "You are the risk arbiter for a strict prop-firm challenge on Volatility 75, a "
    "driftless random walk at constant 75% annualised volatility. Entry rules on this "
    "instrument have exactly zero expectancy, so you cannot improve edge — your only "
    "levers are cutting risk and skipping trades that crowd a drawdown limit. The "
    "Python core has already sized and placed a candidate; you approve, veto, or tighten "
    "it. Never loosen risk beyond what the core proposed. Respond only with the JSON "
    "schema provided: action 'trade' or 'no_trade', optional risk_pct (<= the "
    "candidate's), optional sl/tp, confidence 0..1, and a one-line reasoning."
)


class DecisionProvider(Protocol):
    def decide(self, packet: dict) -> Decision: ...


def approve_all(packet: dict) -> Decision:
    """Default mock policy: approve every candidate unchanged. Module-level (not a
    lambda) so a MockProvider carrying it stays picklable for ProcessPool workers."""
    return Decision(action="trade", confidence=1.0, reasoning="mock approve")


@dataclass
class CostMeter:
    """Running cost + call accounting, shared across a provider's lifetime."""
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    errors: int = 0

    def add(self, in_tok: int, out_tok: int) -> None:
        self.calls += 1
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        self.usd += in_tok * OPUS_INPUT_PER_TOKEN + out_tok * OPUS_OUTPUT_PER_TOKEN

    def summary(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "usd": round(self.usd, 4),
                "errors": self.errors}


@dataclass
class MockProvider:
    """A deterministic provider driven by a plain policy function — no API, no cost.

    `policy(packet) -> Decision`. Default: approve every candidate unchanged. Use a
    custom policy in tests to exercise veto/tighten paths, or as a cheap stand-in
    overlay for the dual-mode comparison.
    """
    policy: Callable[[dict], Decision] = approve_all
    meter: CostMeter = field(default_factory=CostMeter)

    def decide(self, packet: dict) -> Decision:
        self.meter.calls += 1          # counted, but free
        return self.policy(packet)


@dataclass
class OpusProvider:
    """The real Claude overlay: strict JSON, cost accounting, full call logging.

    Requires the `anthropic` package and credentials (see the claude-api docs);
    imported lazily so importing this module never pulls the SDK in. A malformed or
    errored response degrades to a no-trade Decision (§3), never a crash.
    """
    model: str = DEFAULT_MODEL
    max_tokens: int = 1024
    log_path: str | None = None        # JSONL of every prompt+response, if set
    meter: CostMeter = field(default_factory=CostMeter)
    _client: object = field(default=None, init=False, repr=False)

    def _get_client(self):
        if self._client is None:
            import anthropic          # lazy: the sim and tests never import this
            self._client = anthropic.Anthropic()
        return self._client

    def decide(self, packet: dict) -> Decision:
        user = json.dumps(packet, separators=(",", ":"))
        try:
            resp = self._get_client().messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                output_config={"format": {"type": "json_schema",
                                          "schema": DECISION_JSON_SCHEMA}},
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:       # network/API failure ⇒ no trade, logged
            self.meter.errors += 1
            self._log(packet, None, error=str(exc))
            return Decision(malformed=True, reasoning=f"api error: {exc}")

        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.meter.add(getattr(usage, "input_tokens", 0),
                           getattr(usage, "output_tokens", 0))
        decision = parse_decision(text)
        self._log(packet, text, decision=decision)
        return decision

    def _log(self, packet: dict, response: str | None, *,
             decision: Decision | None = None, error: str | None = None) -> None:
        if not self.log_path:
            return
        record = {"ts": time.time(), "model": self.model, "packet": packet,
                  "response": response, "error": error}
        if decision is not None:
            record["decision"] = {"action": decision.action,
                                  "malformed": decision.malformed,
                                  "confidence": decision.confidence}
        Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a") as fh:
            fh.write(json.dumps(record) + "\n")
