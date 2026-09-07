"""Continuous forward tick recorder (polling).

Deriv serves only the last **24 hours** of tick history (measured, not documented).
Candles reach back 365 days. Tick-accurate history therefore cannot be back-filled:
any tick not captured as it happens is gone permanently. Since the spec mandates
tick-level simulation (section 5), this recorder is the long pole of the project
and should run continuously from as early as possible.

Why polling rather than subscribing: from this client country Deriv rejects both
`{"ticks": SYM, "subscribe": 1}` and `ticks_history` with `subscribe: 1` as
`InvalidSymbol`, the same jurisdictional gate that empties `active_symbols`. Plain
`ticks_history` is unaffected. So we poll it on a short cycle with heavy overlap
and de-duplicate on epoch -- overlap makes gaps impossible as long as one poll
lands per overlap window.

Budget: 3 symbols on a 20s cycle is 9 requests/minute against a 45/min allowance.

Design: buffer in memory, flush timestamped parquet chunks, back off on failure.
Chunks are compacted separately so a crash mid-write cannot corrupt the store.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from propfirm.config import DATA_RAW
from propfirm.data.deriv_client import DerivClient

CHUNK_DIR = DATA_RAW / "tick_stream"
POLL_INTERVAL_S = 20.0
POLL_COUNT = 200          # >= interval / tick_rate, with wide margin
FLUSH_EVERY = 2000
BACKOFF_BASE_S = 5.0
BACKOFF_MAX_S = 120.0


def chunk_dir(symbol: str, base: Path = CHUNK_DIR) -> Path:
    return base / symbol


def _flush(symbol: str, rows: dict[int, float]) -> Path | None:
    if not rows:
        return None
    d = chunk_dir(symbol)
    d.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(sorted(rows.items()), columns=["epoch", "price"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = d / f"{stamp}.parquet"
    df.to_parquet(path, index=False, compression="zstd")
    return path


async def record(symbols: list[str], stop: asyncio.Event, verbose: bool = True) -> None:
    """Poll live ticks for `symbols` until `stop` is set."""
    buffers: dict[str, dict[int, float]] = {s: {} for s in symbols}
    seen_max: dict[str, int] = {s: 0 for s in symbols}
    totals: dict[str, int] = {s: 0 for s in symbols}
    backoff = BACKOFF_BASE_S

    if verbose:
        print(f"[{datetime.now(timezone.utc):%H:%M:%S}] polling {symbols} every "
              f"{POLL_INTERVAL_S:g}s (count={POLL_COUNT})", flush=True)

    while not stop.is_set():
        client = DerivClient()
        try:
            await client.connect()
            backoff = BACKOFF_BASE_S
            while not stop.is_set():
                cycle_start = asyncio.get_running_loop().time()
                for sym in symbols:
                    reply = await client.send({
                        "ticks_history": sym, "end": "latest",
                        "count": POLL_COUNT, "style": "ticks",
                    })
                    hist = reply.get("history") or {}
                    for epoch, price in zip(hist.get("times") or [], hist.get("prices") or []):
                        epoch = int(epoch)
                        if epoch <= seen_max[sym]:
                            continue
                        buffers[sym][epoch] = float(price)
                    if buffers[sym]:
                        newest = max(buffers[sym])
                        if len(buffers[sym]) >= FLUSH_EVERY:
                            _flush(sym, buffers[sym])
                            totals[sym] += len(buffers[sym])
                            buffers[sym] = {}
                            if verbose:
                                print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {sym}: "
                                      f"{totals[sym]:,} ticks stored", flush=True)
                        seen_max[sym] = newest

                elapsed = asyncio.get_running_loop().time() - cycle_start
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(),
                                           timeout=max(0.5, POLL_INTERVAL_S - elapsed))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if verbose:
                print(f"[{datetime.now(timezone.utc):%H:%M:%S}] poll error "
                      f"({type(exc).__name__}: {exc}); retry in {backoff:.0f}s", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)
        finally:
            with contextlib.suppress(Exception):
                await client.close()

    for sym, rows in buffers.items():
        if rows:
            _flush(sym, rows)
            totals[sym] += len(rows)
    if verbose:
        print(f"stopped. totals: {totals}", flush=True)


def compact(symbol: str) -> tuple[Path, int] | None:
    """Merge stream chunks into the main tick store, then delete the chunks."""
    from propfirm.data import store

    d = chunk_dir(symbol)
    chunks = sorted(d.glob("*.parquet")) if d.is_dir() else []
    if not chunks:
        return None
    df = pd.concat([pd.read_parquet(c) for c in chunks], ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["epoch"], unit="s", utc=True)
    path, rows = store.write(df, store.tick_path(symbol))
    for c in chunks:
        c.unlink()
    return path, rows
