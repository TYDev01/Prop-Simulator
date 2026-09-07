"""Minimal async Deriv WebSocket v3 client.

Scope is deliberately narrow: request/response for market data. No auth, no
contract purchase. The simulator never touches a real account.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from propfirm.config import (
    DERIV_APP_ID,
    DERIV_WS_URL,
    REQUEST_TIMEOUT_S,
    REQUESTS_PER_MINUTE,
)


class DerivError(RuntimeError):
    """An `error` payload returned by the API."""

    def __init__(self, code: str, message: str, request: dict[str, Any]):
        self.code = code
        self.message = message
        self.request = request
        super().__init__(f"[{code}] {message} (request: {request})")


class RateLimit(DerivError):
    """A throttling error. Transient: the request may succeed on retry.

    Kept distinct from its parent so callers can back off and retry instead of
    mistaking a throttle for a genuine end of history (REMAINING.md §1.1).
    """


# Error codes Deriv returns when it is throttling rather than refusing. Matched
# case-insensitively; the API has used more than one spelling over time.
_RATE_LIMIT_CODES = {"ratelimit", "toomanyrequests"}


@dataclass
class _RateLimiter:
    """Sliding-window throttle. Deriv rejects bursts rather than queueing them."""

    per_minute: int
    _stamps: deque[float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._stamps = deque()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            while self._stamps and now - self._stamps[0] > 60.0:
                self._stamps.popleft()
            if len(self._stamps) < self.per_minute:
                self._stamps.append(now)
                return
            await asyncio.sleep(60.0 - (now - self._stamps[0]) + 0.01)


class DerivClient:
    """One connection, correlated request/response via req_id.

    Usage:
        async with DerivClient() as client:
            symbols = await client.send({"active_symbols": "brief"})
    """

    def __init__(self, app_id: str = DERIV_APP_ID, url: str = DERIV_WS_URL):
        self.url = f"{url}?app_id={app_id}"
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._limiter = _RateLimiter(REQUESTS_PER_MINUTE)

    async def __aenter__(self) -> "DerivClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        # Imported lazily so the simulator, tests, and offline analysis do not
        # need the live-data dependency installed.
        import websockets

        self._ws = await websockets.connect(self.url, ping_interval=20, ping_timeout=20)
        self._reader = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
            try:
                await self._reader
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                req_id = (msg.get("echo_req") or {}).get("req_id")
                future = self._pending.pop(req_id, None) if req_id is not None else None
                if future and not future.done():
                    future.set_result(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # surface connection death to every waiter
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(exc)
            self._pending.clear()

    async def send(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send one request, await its correlated response, raise on API error."""
        if self._ws is None:
            raise RuntimeError("client not connected")
        await self._limiter.acquire()

        self._req_id += 1
        req_id = self._req_id
        payload = {**request, "req_id": req_id}

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        await self._ws.send(json.dumps(payload))

        try:
            msg = await asyncio.wait_for(future, timeout=REQUEST_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"no response within {REQUEST_TIMEOUT_S}s for {request}")

        if "error" in msg:
            err = msg["error"]
            code = err.get("code", "?")
            message = err.get("message", "?")
            cls = RateLimit if str(code).lower() in _RATE_LIMIT_CODES else DerivError
            raise cls(code, message, request)
        return msg
