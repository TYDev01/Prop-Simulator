"""Control strategies (spec section 9.1).

These are not baselines to be beaten for form's sake -- on a driftless instrument
they are the *decisive* test. Section 2.2 says every reward-to-risk ratio has
exactly zero expectancy, so any strategy whose entries carry no information must
produce the same distribution as random entry with identical risk management.

If a candidate strategy cannot beat this over 500+ trades, its apparent edge is its
risk management, not its entries. Running it continuously rather than once is what
stops the research loop from congratulating itself on noise.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from propfirm.config import per_bar_sigma
from propfirm.sim.engine import Context


@dataclass
class RandomEntry:
    """Random direction, random timing, fully specified risk management.

    Everything except the entry decision is identical to a real strategy: same
    sizing rule, same stop geometry, same reward-to-risk, same one-position limit.
    That isolation is the whole point -- the only difference is whether the entry
    carries information.
    """

    risk_pct: float = 1.0            # % of equity risked per trade
    rr: float = 2.0                  # reward-to-risk
    stop_sigmas: float = 1.5         # stop distance in per-bar sigmas
    bar_seconds: int = 3600          # the timeframe whose sigma sets the stop
    trades_per_day: float = 3.0
    seed: int = 0

    _rng: np.random.Generator = field(init=False, repr=False)
    _next_entry_epoch: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)

    def _schedule(self, epoch: int) -> None:
        """Poisson arrivals: memoryless, so entry timing carries no information."""
        mean_gap = 86400.0 / max(self.trades_per_day, 1e-9)
        self._next_entry_epoch = epoch + int(self._rng.exponential(mean_gap))

    def on_tick(self, ctx: Context) -> None:
        if self._next_entry_epoch is None:
            self._schedule(ctx.view.epoch)
            return
        if ctx.ledger.positions:                 # one position at a time
            return
        if ctx.view.epoch < self._next_entry_epoch:
            return
        self._schedule(ctx.view.epoch)

        mid = ctx.mid
        stop_dist = self.stop_sigmas * per_bar_sigma(self.bar_seconds) * mid
        if stop_dist <= 0:
            return

        risk_amount = ctx.equity * self.risk_pct / 100.0
        # Never risk more than the distance remaining to the daily loss floor.
        risk_amount = min(risk_amount, max(0.0, ctx.room_to_daily_loss() * 0.9))
        if risk_amount <= 0:
            return

        lots = ctx.engine.spec.lots_for_risk(risk_amount, stop_dist)
        if lots < ctx.engine.spec.min_lot:
            return

        direction = 1 if self._rng.random() < 0.5 else -1
        entry = ctx.engine.fills.entry_price(direction, mid)
        sl = entry - direction * stop_dist
        tp = entry + direction * stop_dist * self.rr
        (ctx.buy if direction > 0 else ctx.sell)(lots, sl=sl, tp=tp, tag="random")


@dataclass
class NoTrade:
    """Does nothing. Confirms the harness itself neither gains nor loses money."""

    def on_tick(self, ctx: Context) -> None:
        return


@dataclass(frozen=True)
class RandomEntryFactory:
    """Picklable factory so Monte Carlo workers can build strategies per trial.

    Each trial gets its own seed, so the entry randomness varies across accounts
    while every risk parameter stays fixed -- which is what makes a sweep over
    risk_pct a clean experiment rather than a confounded one.
    """

    risk_pct: float = 1.0
    rr: float = 2.0
    stop_sigmas: float = 1.5
    bar_seconds: int = 3600
    trades_per_day: float = 3.0

    def __call__(self, seed: int) -> RandomEntry:
        return RandomEntry(risk_pct=self.risk_pct, rr=self.rr,
                           stop_sigmas=self.stop_sigmas,
                           bar_seconds=self.bar_seconds,
                           trades_per_day=self.trades_per_day, seed=seed)
