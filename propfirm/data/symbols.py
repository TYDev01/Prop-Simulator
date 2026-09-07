"""Live symbol verification.

Spec section 4: symbol codes are hypotheses, verified live -- never trusted blind.

Verification is done by probing `ticks_history` directly rather than by reading
`active_symbols`. Reason (measured 2026-09-05): from some client countries Deriv
returns an EMPTY active_symbols list, because that endpoint reports what is
*tradable* in the caller's jurisdiction. Market data remains fully readable. Since
we only ever consume data -- the simulator never touches a real account -- probing
the data endpoint is both sufficient and a stricter test: it proves the code
returns the series we are about to depend on.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from propfirm.config import CANDIDATE_GRANULARITIES, CANDIDATE_SYMBOLS, DATA_RAW
from propfirm.data.deriv_client import DerivClient, DerivError

REGISTRY_PATH = DATA_RAW / "symbol_registry.json"


@dataclass
class SymbolSpec:
    """What the live API actually serves for an instrument."""

    alias: str
    symbol: str
    last_price: float
    median_tick_interval_s: float
    granularities: list[int]
    display_name: str = ""
    market: str = ""
    submarket: str = ""
    pip: float | None = None
    metadata_available: bool = False


async def fetch_active_symbols(client: DerivClient) -> list[dict[str, Any]]:
    """May legitimately return [] under jurisdictional restriction. Not an error."""
    reply = await client.send({"active_symbols": "brief", "product_type": "basic"})
    return reply["active_symbols"]


async def probe_symbol(client: DerivClient, code: str, n_ticks: int = 60) -> dict | None:
    """Confirm a code serves data. Returns last price and observed tick spacing."""
    try:
        reply = await client.send(
            {"ticks_history": code, "end": "latest", "count": n_ticks, "style": "ticks"}
        )
    except DerivError:
        return None
    hist = reply.get("history") or {}
    prices, times = hist.get("prices") or [], hist.get("times") or []
    if not prices:
        return None
    gaps = [t2 - t1 for t1, t2 in zip(times, times[1:])] if len(times) > 1 else []
    return {
        "last_price": float(prices[-1]),
        "median_tick_interval_s": float(statistics.median(gaps)) if gaps else 0.0,
    }


async def probe_granularities(
    client: DerivClient, code: str, candidates: list[int] | None = None
) -> list[int]:
    """Ask the API which candle widths it actually serves for this symbol."""
    supported: list[int] = []
    for g in candidates or CANDIDATE_GRANULARITIES:
        try:
            reply = await client.send(
                {
                    "ticks_history": code,
                    "end": "latest",
                    "count": 1,
                    "style": "candles",
                    "granularity": g,
                }
            )
            if reply.get("candles"):
                supported.append(g)
        except DerivError:
            continue
    return supported


async def resolve_symbols(
    client: DerivClient, probe_grans: bool = True
) -> tuple[dict[str, SymbolSpec], list[str]]:
    """Match candidate codes against live data. Returns (resolved, unresolved)."""
    try:
        active = await fetch_active_symbols(client)
    except DerivError:
        active = []
    meta = {s["symbol"]: s for s in active}

    resolved: dict[str, SymbolSpec] = {}
    unresolved: list[str] = []

    for alias, candidates in CANDIDATE_SYMBOLS.items():
        hit = None
        for code in candidates:
            probed = await probe_symbol(client, code)
            if probed:
                hit = (code, probed)
                break
        if hit is None:
            unresolved.append(alias)
            continue

        code, probed = hit
        m = meta.get(code, {})
        resolved[alias] = SymbolSpec(
            alias=alias,
            symbol=code,
            last_price=probed["last_price"],
            median_tick_interval_s=probed["median_tick_interval_s"],
            granularities=await probe_granularities(client, code) if probe_grans else [],
            display_name=m.get("display_name", ""),
            market=m.get("market", ""),
            submarket=m.get("submarket", ""),
            pip=m.get("pip"),
            metadata_available=bool(m),
        )
    return resolved, unresolved


def save_registry(resolved: dict[str, SymbolSpec], path: Path = REGISTRY_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: asdict(v) for k, v in resolved.items()}, indent=2))
    return path


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, SymbolSpec]:
    if not path.exists():
        raise FileNotFoundError(
            f"no symbol registry at {path}; run scripts/verify_symbols.py first"
        )
    return {k: SymbolSpec(**v) for k, v in json.loads(path.read_text()).items()}
