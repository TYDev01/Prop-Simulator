"""Fetch a year of M1 candles -- the base layer for tick synthesis.

Kept separate from fetch_history.py because it is long-running (Deriv caps at 5000
bars/request, so a year of M1 is ~106 requests) and is usually run once.
"""
from __future__ import annotations

import asyncio
import sys

from propfirm.data import store
from propfirm.data.deriv_client import DerivClient
from propfirm.data.history import fetch_candles
from propfirm.data.symbols import load_registry


async def main() -> int:
    sym = load_registry()["vol75"].symbol
    async with DerivClient() as c:
        res = await fetch_candles(c, sym, 60, 600_000, progress=True)
    if res.df.empty:
        print("NO DATA")
        return 1
    _, rows = store.write(res.df, store.candle_path(sym, 60))
    print(f"{sym} M1: {rows:,} bars  {res.df.timestamp.min():%Y-%m-%d} -> "
          f"{res.df.timestamp.max():%Y-%m-%d}  ({res.requests} reqs, "
          f"{'EXHAUSTED' if res.exhausted else 'target met'})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
