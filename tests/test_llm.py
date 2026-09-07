"""Phase 3 Opus overlay: schema, state packet, providers, dual-mode (§3)."""
from __future__ import annotations

import json

import pytest

from propfirm.llm.overlay import OpusOverlay, dual_mode
from propfirm.llm.provider import (
    CostMeter,
    MockProvider,
    OpusProvider,
    OPUS_INPUT_PER_TOKEN,
    OPUS_OUTPUT_PER_TOKEN,
)
from propfirm.llm.schema import DECISION_JSON_SCHEMA, Decision, parse_decision
from propfirm.research.montecarlo import gbm_path
from propfirm.rules.ruleset import STRICT_100K
from propfirm.sim.contract import VOL75
from propfirm.sim.engine import SimEngine
from propfirm.strategy.seed_mae import ReducedMAE


# --- schema validation -------------------------------------------------------

def test_valid_trade_decision_parses():
    d = parse_decision('{"action":"trade","risk_pct":0.5,"sl":100,"tp":120,'
                       '"confidence":0.7,"reasoning":"ok"}')
    assert d.is_trade
    assert d.risk_pct == 0.5 and d.confidence == 0.7 and not d.malformed


@pytest.mark.parametrize("raw", [
    "not json at all",
    "[1, 2, 3]",                                  # valid JSON, not an object
    '{"action":"maybe","reasoning":"x"}',         # action not in enum
    '{"reasoning":"no action"}',                  # missing action
])
def test_malformed_responses_degrade_to_no_trade(raw):
    d = parse_decision(raw)
    assert d.malformed
    assert not d.is_trade                          # malformed is never honoured


def test_confidence_is_clamped():
    assert parse_decision('{"action":"no_trade","confidence":5,"reasoning":""}'
                          ).confidence == 1.0
    assert parse_decision('{"action":"no_trade","confidence":-3,"reasoning":""}'
                          ).confidence == 0.0


def test_schema_is_strict_output_shaped():
    assert DECISION_JSON_SCHEMA["additionalProperties"] is False
    assert set(DECISION_JSON_SCHEMA["required"]) == {
        "action", "risk_pct", "sl", "tp", "confidence", "reasoning"}


# --- cost meter --------------------------------------------------------------

def test_cost_meter_accumulates_usd():
    m = CostMeter()
    m.add(1000, 200)
    m.add(500, 100)
    assert m.calls == 2 and m.input_tokens == 1500 and m.output_tokens == 300
    expected = 1500 * OPUS_INPUT_PER_TOKEN + 300 * OPUS_OUTPUT_PER_TOKEN
    assert m.usd == pytest.approx(expected)


# --- OpusProvider with an injected fake client (no anthropic dependency) -----

class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Usage:
    def __init__(self, i, o):
        self.input_tokens, self.output_tokens = i, o


class _Resp:
    def __init__(self, text, i=1000, o=50):
        self.content, self.usage = [_Block(text)], _Usage(i, o)


class _FakeMessages:
    def __init__(self, text=None, raise_exc=None):
        self._text, self._raise = text, raise_exc

    def create(self, **kwargs):
        if self._raise:
            raise self._raise
        return _Resp(self._text)


class _FakeClient:
    def __init__(self, text=None, raise_exc=None):
        self.messages = _FakeMessages(text, raise_exc)


def _opus_with(client) -> OpusProvider:
    p = OpusProvider()
    p._client = client                             # bypass the lazy anthropic import
    return p


def test_opus_provider_parses_and_meters(tmp_path):
    log = tmp_path / "calls.jsonl"
    p = _opus_with(_FakeClient('{"action":"trade","risk_pct":null,"sl":null,'
                               '"tp":null,"confidence":0.9,"reasoning":"go"}'))
    p.log_path = str(log)
    d = p.decide({"candidate": {}, "account": {}})
    assert d.is_trade
    assert p.meter.calls == 1 and p.meter.input_tokens == 1000
    # The full prompt+response is logged as the research dataset (§14).
    line = json.loads(log.read_text().strip())
    assert line["response"].startswith("{") and line["decision"]["action"] == "trade"


def test_opus_provider_api_error_is_a_no_trade():
    p = _opus_with(_FakeClient(raise_exc=RuntimeError("boom")))
    d = p.decide({"candidate": {}})
    assert d.malformed and not d.is_trade
    assert p.meter.errors == 1


def test_opus_provider_malformed_response_is_a_no_trade():
    p = _opus_with(_FakeClient("this is not json"))
    d = p.decide({"candidate": {}})
    assert d.malformed and not d.is_trade


# --- overlay behaviour on a real run -----------------------------------------

def _run(strategy):
    ticks = gbm_path(30.0, seed=4, tick_seconds=30)
    return SimEngine(spec=VOL75, rules=STRICT_100K).run(ticks, strategy)


def test_overlay_approve_matches_core():
    core = _run(ReducedMAE(risk_pct=1.0, rr=2.0))
    approve = _run(ReducedMAE(risk_pct=1.0, rr=2.0, decide=OpusOverlay(MockProvider())))
    assert approve.trades == core.trades
    assert [t.pnl for t in approve.ledger.closed] == [t.pnl for t in core.ledger.closed]


def test_overlay_veto_blocks_all_trades():
    veto = OpusOverlay(MockProvider(
        policy=lambda packet: Decision(action="no_trade", reasoning="veto")))
    r = _run(ReducedMAE(risk_pct=1.0, rr=2.0, decide=veto))
    assert r.trades == 0
    assert veto.provider.meter.calls > 0           # candidate-triggered, was consulted


def test_overlay_tightens_risk():
    # A provider that halves risk should produce smaller positions than the core.
    half = OpusOverlay(MockProvider(
        policy=lambda packet: Decision(action="trade", risk_pct=0.5, reasoning="half")))
    core = _run(ReducedMAE(risk_pct=1.0, rr=2.0))
    tight = _run(ReducedMAE(risk_pct=1.0, rr=2.0, decide=half))
    core_lots = [t.lots for t in core.ledger.closed]
    tight_lots = [t.lots for t in tight.ledger.closed]
    assert core_lots and tight_lots
    assert tight_lots[0] == pytest.approx(core_lots[0] / 2, rel=0.05)


def test_overlay_cannot_raise_risk_above_the_core():
    # A provider asking for 10x risk must be clamped to the core's size.
    greedy = OpusOverlay(MockProvider(
        policy=lambda packet: Decision(action="trade", risk_pct=10.0, reasoning="more")))
    core = _run(ReducedMAE(risk_pct=1.0, rr=2.0))
    got = _run(ReducedMAE(risk_pct=1.0, rr=2.0, decide=greedy))
    assert [t.lots for t in got.ledger.closed] == [t.lots for t in core.ledger.closed]


def test_dual_mode_shares_config():
    core_fac, overlay_fac = dual_mode(MockProvider(), risk_pct=1.0, rr=2.0)
    assert core_fac.risk_pct == overlay_fac.risk_pct == 1.0
    assert core_fac.decide is None and overlay_fac.decide is not None
