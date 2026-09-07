"""Backwards-paging history fetcher.

Deriv caps a ticks_history response at 5000 rows, so deep history is built by
walking `end` backwards until the API stops yielding earlier data.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pandas as pd

from propfirm.config import MAX_ROWS_PER_REQUEST
from propfirm.data.deriv_client import DerivClient, RateLimit

# Rate-limit backoff. Deriv throttles bursts rather than queueing them, so a
# RateLimit mid-fetch is transient and worth retrying before giving up.
_MAX_RATE_LIMIT_RETRIES = 5
_BACKOFF_BASE_S = 1.0


async def _send_with_backoff(client: DerivClient, request: dict, progress: bool):
    """Send a request, retrying RateLimit with exponential backoff.

    Only RateLimit is retried. Any other DerivError propagates -- a bad symbol or
    parameter is a real failure and must not be silently swallowed into a partial
    result flagged 'exhausted' (REMAINING.md §1.1).
    """
    for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
        try:
            return await client.send(request)
        except RateLimit:
            if attempt >= _MAX_RATE_LIMIT_RETRIES:
                raise
            wait = _BACKOFF_BASE_S * (2 ** attempt)
            if progress:
                print(f"    rate limited; backing off {wait:.0f}s "
                      f"(retry {attempt + 1}/{_MAX_RATE_LIMIT_RETRIES})", flush=True)
            await asyncio.sleep(wait)


@dataclass
class FetchResult:
    df: pd.DataFrame
    requests: int
    exhausted: bool  # True only if the API genuinely ran out of history


async def fetch_candles(
    client: DerivClient, symbol: str, granularity: int, target_bars: int,
    progress: bool = False,
) -> FetchResult:
    frames: list[pd.DataFrame] = []
    end: int | str = "latest"
    seen = 0
    requests = 0
    exhausted = False

    while seen < target_bars:
        want = min(MAX_ROWS_PER_REQUEST, target_bars - seen)
        reply = await _send_with_backoff(client, {
            "ticks_history": symbol, "end": end, "count": want,
            "style": "candles", "granularity": granularity,
        }, progress)
        requests += 1
        candles = reply.get("candles") or []
        if not candles:
            exhausted = True
            break

        df = pd.DataFrame(candles)
        frames.append(df)
        seen += len(df)
        oldest = int(df["epoch"].min())
        if progress:
            print(f"    {symbol} g{granularity}: {seen:,} bars, back to "
                  f"{pd.to_datetime(oldest, unit='s', utc=True):%Y-%m-%d}", flush=True)
        # Step strictly before the oldest bar we have, else we loop forever.
        new_end = oldest - 1
        if end != "latest" and new_end >= int(end):
            exhausted = True
            break
        end = new_end
        if len(df) < want:
            exhausted = True
            break

    if not frames:
        return FetchResult(pd.DataFrame(), requests, True)
    out = (pd.concat(frames, ignore_index=True)
             .drop_duplicates(subset=["epoch"]).sort_values("epoch").reset_index(drop=True))
    out["timestamp"] = pd.to_datetime(out["epoch"], unit="s", utc=True)
    return FetchResult(out, requests, exhausted)


async def fetch_ticks(
    client: DerivClient, symbol: str, target_ticks: int, progress: bool = False,
) -> FetchResult:
    frames: list[pd.DataFrame] = []
    end: int | str = "latest"
    seen = 0
    requests = 0
    exhausted = False

    while seen < target_ticks:
        want = min(MAX_ROWS_PER_REQUEST, target_ticks - seen)
        reply = await _send_with_backoff(client, {
            "ticks_history": symbol, "end": end, "count": want, "style": "ticks",
        }, progress)
        requests += 1
        hist = reply.get("history") or {}
        prices, times = hist.get("prices") or [], hist.get("times") or []
        if not prices:
            exhausted = True
            break

        df = pd.DataFrame({"epoch": times, "price": prices})
        frames.append(df)
        seen += len(df)
        oldest = int(df["epoch"].min())
        if progress and requests % 20 == 0:
            print(f"    {symbol}: {seen:,} ticks, back to "
                  f"{pd.to_datetime(oldest, unit='s', utc=True):%Y-%m-%d %H:%M}", flush=True)
        new_end = oldest - 1
        if end != "latest" and new_end >= int(end):
            exhausted = True
            break
        end = new_end
        if len(df) < want:
            exhausted = True
            break

    if not frames:
        return FetchResult(pd.DataFrame(), requests, True)
    out = (pd.concat(frames, ignore_index=True)
             .drop_duplicates(subset=["epoch"]).sort_values("epoch").reset_index(drop=True))
    out["timestamp"] = pd.to_datetime(out["epoch"], unit="s", utc=True)
    return FetchResult(out, requests, exhausted)
