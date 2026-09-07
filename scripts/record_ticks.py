"""Run the forward tick recorder. Ctrl-C or SIGTERM to stop cleanly.

Deriv keeps only 24h of tick history, so this must run continuously to build the
tick-accurate dataset the simulator needs. See propfirm/data/recorder.py.
"""
from __future__ import annotations

import asyncio
import signal
import sys

from propfirm.data.recorder import record
from propfirm.data.symbols import load_registry

ALIASES = ["vol75", "boom1000", "crash1000"]


async def main() -> int:
    reg = load_registry()
    symbols = [reg[a].symbol for a in ALIASES if a in reg]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    print(f"recording {symbols} -- Deriv retains only 24h of tick history, "
          f"so this is the only way to build depth", flush=True)
    await record(symbols, stop)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
