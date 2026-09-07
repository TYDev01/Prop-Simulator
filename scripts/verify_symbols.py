"""Phase 0 gate: verify candidate symbol codes against the live API.

Writes data/raw/symbol_registry.json. Everything downstream reads that file.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from propfirm.config import per_bar_sigma
from propfirm.data.deriv_client import DerivClient
from propfirm.data.symbols import fetch_active_symbols, resolve_symbols, save_registry


async def main() -> int:
    async with DerivClient() as client:
        status = (await client.send({"website_status": 1}))["website_status"]
        active = await fetch_active_symbols(client)
        print(f"site={status.get('site_status')} client_country={status.get('clients_country')}")
        print(f"active_symbols: {len(active)} entries", end="")
        print("  <- EMPTY: jurisdictional restriction on the tradable list;"
              " market data verified by direct probe instead\n" if not active else "\n")

        resolved, unresolved = await resolve_symbols(client)
        for alias, s in resolved.items():
            print(f"  {alias:12s} -> {s.symbol:10s} last={s.last_price:,.4f}"
                  f"  tick={s.median_tick_interval_s:g}s"
                  f"  metadata={'yes' if s.metadata_available else 'no'}")
            print(f"  {'':12s}    granularities: {s.granularities}")

        if unresolved:
            print(f"\nUNRESOLVED: {unresolved}")
            return 1

        print(f"\nregistry written: {save_registry(resolved)}")

        print("\nVol75 per-bar sigma (75% annualised), as fraction of price:")
        px = resolved["vol75"].last_price
        for label, g in [("M5", 300), ("M15", 900), ("H1", 3600), ("H4", 14400), ("D1", 86400)]:
            sig = per_bar_sigma(g)
            print(f"  {label:3s} {sig*100:6.3f}%   ~{sig*px:>10,.1f} index points at {px:,.0f}")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
