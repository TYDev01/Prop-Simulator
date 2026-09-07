"""History paging: rate limits retry, real errors propagate (REMAINING.md §1.1).

The old code turned *any* DerivError into a silent 'exhausted=True', so a transient
throttle was indistinguishable from a genuine end of history -- and the measured
depth limits were established through exactly that path. These tests pin the fixed
behaviour: a RateLimit is retried, a genuine empty response ends paging, and any
other error surfaces instead of masquerading as depth.
"""
from __future__ import annotations

import asyncio

import pytest

from propfirm.data import history
from propfirm.data.deriv_client import DerivError, RateLimit


class FakeClient:
    """Replays a scripted sequence of send() outcomes.

    Each script item is either ('raise', exc) or ('return', reply_dict).
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def send(self, request):
        self.calls += 1
        action, payload = self.script.pop(0)
        if action == "raise":
            raise payload
        return payload


@pytest.fixture(autouse=True)
def _no_real_backoff(monkeypatch):
    # Keep the exponential backoff logic but make its sleeps instant.
    monkeypatch.setattr(history, "_BACKOFF_BASE_S", 0.0)


def _rl():
    return RateLimit("RateLimit", "slow down", {})


def _tick_reply(prices, times):
    return ("return", {"history": {"prices": prices, "times": times}})


# --- the backoff helper ------------------------------------------------------

def test_rate_limit_is_retried_then_succeeds():
    client = FakeClient([("raise", _rl()), ("raise", _rl()), ("return", {"ok": 1})])
    out = asyncio.run(history._send_with_backoff(client, {"q": 1}, progress=False))
    assert out == {"ok": 1}
    assert client.calls == 3


def test_rate_limit_gives_up_after_the_retry_budget():
    client = FakeClient([("raise", _rl())] * 20)
    with pytest.raises(RateLimit):
        asyncio.run(history._send_with_backoff(client, {"q": 1}, progress=False))
    assert client.calls == history._MAX_RATE_LIMIT_RETRIES + 1


def test_non_rate_limit_error_propagates_immediately():
    client = FakeClient([("raise", DerivError("InvalidSymbol", "nope", {}))])
    with pytest.raises(DerivError):
        asyncio.run(history._send_with_backoff(client, {"q": 1}, progress=False))
    assert client.calls == 1


# --- fetch_ticks integration -------------------------------------------------

def test_short_page_ends_paging_as_genuinely_exhausted():
    # One page of 5 (< the 10 requested) is a real end of history.
    client = FakeClient([_tick_reply([1, 2, 3, 4, 5], [100, 101, 102, 103, 104])])
    res = asyncio.run(history.fetch_ticks(client, "R_75", target_ticks=10))
    assert res.exhausted is True
    assert len(res.df) == 5
    assert res.requests == 1


def test_fetch_ticks_surfaces_a_real_error_instead_of_faking_exhaustion():
    client = FakeClient([("raise", DerivError("InvalidSymbol", "nope", {}))])
    with pytest.raises(DerivError):
        asyncio.run(history.fetch_ticks(client, "BAD", target_ticks=10))


def test_fetch_ticks_retries_a_throttle_then_completes():
    client = FakeClient([
        ("raise", _rl()),
        _tick_reply([1, 2, 3], [100, 101, 102]),   # short page -> exhausted
    ])
    res = asyncio.run(history.fetch_ticks(client, "R_75", target_ticks=10))
    assert client.calls == 2
    assert len(res.df) == 3
    assert res.exhausted is True
