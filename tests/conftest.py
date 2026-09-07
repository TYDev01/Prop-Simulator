"""Shared fixtures and small builders for the test suite."""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from propfirm.sim.contract import VOL75, ContractSpec


def ticks_from_prices(prices, epoch0: int = 1_780_000_000, step: int = 2) -> pd.DataFrame:
    """Build an [epoch, price] frame from a price sequence at a fixed tick rate."""
    prices = np.asarray(prices, dtype=float)
    epochs = np.arange(len(prices)) * step + epoch0
    return pd.DataFrame({"epoch": epochs, "price": prices})


@pytest.fixture
def vol75() -> ContractSpec:
    return VOL75


def spec_with(**overrides) -> ContractSpec:
    """A VOL75 clone with selected fields replaced (VOL75 is frozen)."""
    return dataclasses.replace(VOL75, **overrides)
