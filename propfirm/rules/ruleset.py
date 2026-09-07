"""Prop firm rulesets as data (spec section 6).

Rules are configuration, never code, so that a different firm is a different file
rather than a different program. The default here is the strict variant the spec
commits to: equity-trailing drawdown, a consistency cap, and a minimum trading-day
count.

Two of these choices are load-bearing rather than decorative:

  trail_on = EQUITY   Floating losses count against the limit immediately and
                      floating profit ratchets it upward. This is the harshest
                      common variant and it kills "let it breathe" approaches.

  min_trading_days +  These block the degenerate solution (spec section 9.2). With
  consistency cap     zero edge, spread cost scales with trade count, so the
                      mathematically optimal challenge strategy is one huge bet.
                      These two rules are what force something resembling trading.
                      Do not relax them for convenience.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TrailOn(str, Enum):
    EQUITY = "equity"
    BALANCE = "balance"
    NONE = "static"


@dataclass(frozen=True)
class PhaseRules:
    name: str
    profit_target_pct: float | None      # None => funded, no target
    max_days: int | None


@dataclass(frozen=True)
class FirmRules:
    name: str
    account_size: float
    max_daily_loss_pct: float
    max_total_loss_pct: float
    trail_on: TrailOn
    min_trading_days: int
    consistency_max_day_pct: float | None   # max share of total profit from one day
    phases: tuple[PhaseRules, ...]
    profit_split: float                      # trader's share, e.g. 0.80
    payout_cycle_days: int
    challenge_fee: float
    broker_utc_offset_hours: int = 2

    def phase(self, index: int) -> PhaseRules:
        return self.phases[index]


# The strict default committed to in BUILD_PROMPT.md section 6.
STRICT_100K = FirmRules(
    name="strict-100k",
    account_size=100_000.0,
    max_daily_loss_pct=5.0,
    max_total_loss_pct=10.0,
    trail_on=TrailOn.EQUITY,
    min_trading_days=4,
    consistency_max_day_pct=40.0,
    phases=(
        PhaseRules("phase1", profit_target_pct=8.0, max_days=30),
        PhaseRules("phase2", profit_target_pct=5.0, max_days=60),
        PhaseRules("funded", profit_target_pct=None, max_days=None),
    ),
    profit_split=0.80,
    payout_cycle_days=14,
    challenge_fee=500.0,
)

# A softer comparison ruleset: static drawdown, no consistency cap. Useful for
# isolating how much of the pass rate the strict rules actually cost.
LENIENT_100K = FirmRules(
    name="lenient-100k",
    account_size=100_000.0,
    max_daily_loss_pct=5.0,
    max_total_loss_pct=10.0,
    trail_on=TrailOn.NONE,
    min_trading_days=0,
    consistency_max_day_pct=None,
    phases=(
        PhaseRules("phase1", profit_target_pct=8.0, max_days=30),
        PhaseRules("phase2", profit_target_pct=5.0, max_days=60),
        PhaseRules("funded", profit_target_pct=None, max_days=None),
    ),
    profit_split=0.80,
    payout_cycle_days=14,
    challenge_fee=500.0,
)

RULESETS = {r.name: r for r in (STRICT_100K, LENIENT_100K)}
