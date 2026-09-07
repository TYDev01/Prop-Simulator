"""Populate the immutable raw store.

Vol75 candles at the working timeframes, plus Boom/Crash ticks for the spike
hazard study (spec section 12 -- run early, it can reorder the roadmap).
"""
from __future__ import annotations

import asyncio
import sys

from propfirm.data import store
from propfirm.data.deriv_client import DerivClient
from propfirm.data.history import fetch_candles, fetch_ticks
from propfirm.data.symbols import load_registry

CANDLE_PLAN = [("vol75", 86400, 2000), ("vol75", 14400, 12000),
               ("vol75", 3600, 40000), ("vol75", 900, 40000)]
TICK_PLAN = [("boom1000", 1_500_000), ("crash1000", 1_500_000), ("vol75", 500_000)]


async def main() -> int:
    reg = load_registry()
    async with DerivClient() as client:
        print("=== candles ===", flush=True)
        for alias, gran, target in CANDLE_PLAN:
            sym = reg[alias].symbol
            res = await fetch_candles(client, sym, gran, target, progress=True)
            if res.df.empty:
                print(f"  {sym} g{gran}: NO DATA"); continue
            path, rows = store.write(res.df, store.candle_path(sym, gran))
            span = f"{res.df.timestamp.min():%Y-%m-%d} -> {res.df.timestamp.max():%Y-%m-%d}"
            print(f"  {sym} g{gran}: {rows:,} bars  {span}"
                  f"  ({res.requests} reqs{', EXHAUSTED' if res.exhausted else ''})", flush=True)

        print("\n=== ticks ===", flush=True)
        for alias, target in TICK_PLAN:
            sym = reg[alias].symbol
            res = await fetch_ticks(client, sym, target, progress=True)
            if res.df.empty:
                print(f"  {sym}: NO DATA"); continue
            path, rows = store.write(res.df, store.tick_path(sym))
            span = f"{res.df.timestamp.min():%Y-%m-%d %H:%M} -> {res.df.timestamp.max():%Y-%m-%d %H:%M}"
            print(f"  {sym}: {rows:,} ticks  {span}"
                  f"  ({res.requests} reqs{', EXHAUSTED' if res.exhausted else ''})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
