"""Reader for MetaTrader 5 `ticks.dat` tick-cache files.

Why this exists: Deriv's `ticks_history` WebSocket endpoint returns a single
price per tick with no bid/ask, so it cannot tell us the spread -- and spread is
the tax on every trade and the number that decides our working timeframe
(spec section 4). A locally cached MT5 terminal connected to Deriv-Demo does
carry real bid/ask, so we read the broker's own record.

Format was reverse-engineered from the files themselves rather than assumed:
a 432-byte header followed by packed 60-byte MqlTick records.

    offset  type     field
    +0      int64    time (epoch seconds)
    +8      double   bid
    +16     double   ask
    +24     double   last
    +32     int64    volume
    +40     int64    time_msc (epoch milliseconds)
    +48     uint32   flags
    +52     double   volume_real

This is a fixed-size ring buffer of *recent* ticks, not deep history. Treat it
as a spread probe, not as a price series.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

MT5_BASE = Path(
    "/home/tony/.mt5/drive_c/Program Files/MetaTrader 5/Bases"
)

HEADER_BYTES = 432
RECORD_BYTES = 60
_RECORD = struct.Struct("<q3dqqI d")  # 60 bytes: see module docstring

EPOCH_MIN, EPOCH_MAX = 1_500_000_000, 2_000_000_000


@dataclass(frozen=True)
class TickFile:
    server: str
    symbol: str
    path: Path


def find_tick_files(server: str = "Deriv-Demo", base: Path = MT5_BASE) -> list[TickFile]:
    root = base / server / "ticks"
    if not root.is_dir():
        return []
    return [
        TickFile(server, d.name, d / "ticks.dat")
        for d in sorted(root.iterdir())
        if (d / "ticks.dat").is_file()
    ]


def read_ticks(path: Path) -> pd.DataFrame:
    """Parse one ticks.dat into a DataFrame of [time, bid, ask, last, spread].

    Records failing a sanity check (implausible epoch, non-positive or crossed
    quotes) are dropped rather than repaired -- a ring buffer legitimately
    contains uninitialised slots.
    """
    raw = path.read_bytes()
    body = raw[HEADER_BYTES:]
    n = len(body) // RECORD_BYTES

    rows = []
    for i in range(n):
        chunk = body[i * RECORD_BYTES : (i + 1) * RECORD_BYTES]
        if len(chunk) < RECORD_BYTES:
            break
        t, bid, ask, last, vol, t_msc, flags, vol_real = _RECORD.unpack(chunk)
        if not (EPOCH_MIN <= t <= EPOCH_MAX):
            continue
        if not (bid > 0 and ask > 0) or ask < bid:
            continue
        rows.append((t, bid, ask, last))

    df = pd.DataFrame(rows, columns=["time", "bid", "ask", "last"])
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df["spread"] = df["ask"] - df["bid"]
    df["spread_frac"] = df["spread"] / df["mid"]
    return df.sort_values("time").reset_index(drop=True)
