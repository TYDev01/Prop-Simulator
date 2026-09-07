"""Immutable raw store. Fetch once, derive many times (spec section 4)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from propfirm.config import DATA_RAW


def candle_path(symbol: str, granularity: int, base: Path = DATA_RAW) -> Path:
    return base / "candles" / symbol / f"g{granularity}.parquet"


def tick_path(symbol: str, base: Path = DATA_RAW) -> Path:
    return base / "ticks" / symbol / "ticks.parquet"


def _merge(existing: pd.DataFrame | None, new: pd.DataFrame, key: str) -> pd.DataFrame:
    df = new if existing is None else pd.concat([existing, new], ignore_index=True)
    return df.drop_duplicates(subset=[key]).sort_values(key).reset_index(drop=True)


def write(df: pd.DataFrame, path: Path, key: str = "epoch") -> tuple[Path, int]:
    """Merge into any existing file, de-duplicating on `key`. Returns (path, rows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = pd.read_parquet(path) if path.exists() else None
    merged = _merge(existing, df, key)
    merged.to_parquet(path, index=False, compression="zstd")
    return path, len(merged)


def read(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"no data at {path}")
    return pd.read_parquet(path)
