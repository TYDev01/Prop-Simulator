"""Instrument contract specification for MT5 CFD maths.

Spec section 4: broker facts are ingested, never hardcoded from memory. Where a
field could be measured it carries its measurement; where it could not, it is
flagged UNVERIFIED and must be confirmed against the MT5 terminal's symbol
properties before any result is trusted quantitatively.

Provenance is a field, not a comment, so that an unverified number cannot quietly
propagate into a headline result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Provenance(str, Enum):
    MEASURED = "measured"        # derived from broker data in this repo
    DOCUMENTED = "documented"    # from Deriv published spec
    UNVERIFIED = "unverified"    # assumed; must be confirmed before trusting


@dataclass(frozen=True)
class ContractSpec:
    symbol: str
    display_name: str

    # Quote geometry
    digits: int
    point: float                 # smallest price increment
    spread_points: float         # in `point` units

    # Position sizing / P&L
    contract_size: float         # units of the index per 1.00 lot
    min_lot: float
    max_lot: float
    lot_step: float

    # Risk
    leverage: float              # e.g. 500 => 0.2% margin
    stops_level_points: float    # minimum SL/TP distance from price
    swap_long: float
    swap_short: float

    provenance: dict[str, Provenance] = field(default_factory=dict)

    def unverified_fields(self) -> list[str]:
        return [k for k, v in self.provenance.items() if v is Provenance.UNVERIFIED]

    # --- MT5 CFD maths -------------------------------------------------------

    def pnl(self, direction: int, lots: float, entry: float, exit_: float) -> float:
        """Profit in account currency. direction: +1 long, -1 short."""
        return direction * (exit_ - entry) * lots * self.contract_size

    def margin_required(self, lots: float, price: float) -> float:
        return lots * self.contract_size * price / self.leverage

    def value_per_point(self, lots: float) -> float:
        return lots * self.contract_size * self.point

    def lots_for_risk(self, risk_amount: float, stop_distance: float) -> float:
        """Lot size such that hitting the stop loses `risk_amount`."""
        if stop_distance <= 0:
            return 0.0
        raw = risk_amount / (stop_distance * self.contract_size)
        stepped = round(raw / self.lot_step) * self.lot_step
        return max(self.min_lot, min(self.max_lot, stepped))


# Volatility 75 Index. Spread is MEASURED (docs/phase0_findings.md section 3);
# every sizing field is UNVERIFIED and must be read off the MT5 terminal before
# any P&L number here is quoted as real.
VOL75 = ContractSpec(
    symbol="R_75",
    display_name="Volatility 75 Index",
    digits=4,
    point=0.0001,
    spread_points=45000.0,          # 4.50 index points / 0.0001
    contract_size=1.0,
    min_lot=0.001,
    max_lot=50.0,
    lot_step=0.001,
    leverage=500.0,
    stops_level_points=0.0,
    swap_long=0.0,
    swap_short=0.0,
    provenance={
        "digits": Provenance.MEASURED,        # 4dp seen in live tick quotes
        "spread_points": Provenance.MEASURED, # 4.50 pts, constant over 2048 ticks
        "contract_size": Provenance.UNVERIFIED,
        "min_lot": Provenance.UNVERIFIED,
        "max_lot": Provenance.UNVERIFIED,
        "lot_step": Provenance.UNVERIFIED,
        "leverage": Provenance.UNVERIFIED,
        "stops_level_points": Provenance.UNVERIFIED,
        "swap_long": Provenance.UNVERIFIED,
        "swap_short": Provenance.UNVERIFIED,
    },
)

SPECS = {"R_75": VOL75}
