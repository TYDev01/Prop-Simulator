"""Simulation engine: drives the feed, the strategy, fills, and the rule engine.

Loop order per tick is deliberate and load-bearing:

    1. advance the feed          (cursor moves; the future stays unreachable)
    2. mark the ledger           (day boundary, equity high-water-mark)
    3. resolve open positions    (SL/TP before any new decision)
    4. check prop rules          (hard kill switch, before the strategy runs)
    5. call the strategy         (only if still alive)

Rules are evaluated *before* the strategy so a breached account can never place
another trade, and stops resolve before new decisions so a strategy cannot act on
a position the market has already closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

from propfirm.rules.breach import ChallengeState, Outcome, check
from propfirm.rules.ruleset import FirmRules
from propfirm.sim.contract import ContractSpec
from propfirm.sim.feed import MarketView, TickFeed
from propfirm.sim.fills import FillEngine
from propfirm.sim.ledger import Ledger, Position


@dataclass
class Context:
    """What a strategy is allowed to see and do. No route to the future."""
    view: MarketView
    ledger: Ledger
    state: ChallengeState
    engine: "SimEngine"

    @property
    def mid(self) -> float:
        return self.view.price

    @property
    def equity(self) -> float:
        return self.ledger.equity(self.mid)

    def room_to_daily_loss(self) -> float:
        from propfirm.rules.breach import daily_loss_floor
        return self.equity - daily_loss_floor(self.state, self.ledger)

    def room_to_max_loss(self) -> float:
        from propfirm.rules.breach import max_loss_floor
        return self.equity - max_loss_floor(self.state, self.ledger)

    def buy(self, lots: float, sl: float | None = None, tp: float | None = None,
            tag: str = "") -> Position | None:
        return self.engine.fills.open_market(self.ledger, +1, lots, self.mid,
                                             self.view.epoch, sl, tp, tag)

    def sell(self, lots: float, sl: float | None = None, tp: float | None = None,
             tag: str = "") -> Position | None:
        return self.engine.fills.open_market(self.ledger, -1, lots, self.mid,
                                             self.view.epoch, sl, tp, tag)

    def close(self, pos: Position, reason: str = "manual"):
        exc = self.engine._excursions.pop(id(pos), (0.0, 0.0))
        return self.engine.fills.close_market(self.ledger, pos, self.mid,
                                              self.view.epoch, reason, *exc)


class Strategy(Protocol):
    def on_tick(self, ctx: Context) -> None: ...


@dataclass
class RunResult:
    outcome: Outcome
    detail: str
    ticks: int
    final_equity: float
    peak_equity: float
    trades: int
    trading_days: int
    ledger: Ledger
    state: ChallengeState


@dataclass
class SimEngine:
    spec: ContractSpec
    rules: FirmRules
    fills: FillEngine = field(init=False)
    slippage_points: float = 0.0

    def __post_init__(self) -> None:
        self.fills = FillEngine(spec=self.spec, slippage_points=self.slippage_points)
        self._excursions: dict[int, tuple[float, float]] = {}

    def run(self, ticks: pd.DataFrame, strategy: Strategy, phase_index: int = 0,
            starting_balance: float | None = None) -> RunResult:
        feed = TickFeed(ticks)
        ledger = Ledger(spec=self.spec,
                        starting_balance=starting_balance or self.rules.account_size)
        first_epoch = int(ticks["epoch"].iloc[0])
        state = ChallengeState(rules=self.rules, phase_index=phase_index,
                               start_epoch=first_epoch,
                               start_balance=ledger.starting_balance)
        self._excursions = {}

        prev_mid: float | None = None
        last_epoch = first_epoch
        n = 0
        while True:
            view = feed.step()
            if view is None:
                break
            n += 1
            mid = view.price
            last_epoch = view.epoch

            # Equity is computed once per tick and threaded through both the
            # ledger and the rule check; recomputing it was ~11% of runtime.
            equity = ledger.mark(view.epoch, mid)

            # Resolve stops/targets before anything else can act.
            if prev_mid is not None and ledger.positions:
                for pos in list(ledger.positions):
                    self._track_excursion(pos, mid)
                    hit = self.fills.check_exit(pos, prev_mid, mid)
                    if hit is not None:
                        price, reason = hit
                        mae, mfe = self._excursions.pop(id(pos), (0.0, 0.0))
                        ledger.close(pos, price, view.epoch, reason, mae, mfe)
                # A close moves money from floating to realised, so the equity
                # computed before the loop is stale.
                equity = ledger.equity(mid)

            # Hard kill switch, ahead of any strategy decision.
            outcome = check(state, ledger, mid, view.epoch, equity)
            if outcome is not Outcome.RUNNING:
                for pos in list(ledger.positions):
                    self.fills.close_market(ledger, pos, mid, view.epoch,
                                            reason=f"forced:{outcome.value}")
                break

            strategy.on_tick(Context(view=view, ledger=ledger, state=state, engine=self))
            prev_mid = mid

        # Running out of data without passing IS a failure: the deadline arrived
        # and the target was not met. Leaving these as RUNNING would drop them out
        # of the failure count entirely and flatter the strategy.
        if state.outcome is Outcome.RUNNING and state.phase.max_days is not None:
            state.outcome = Outcome.EXPIRED
            state.breach_detail = "deadline reached without hitting target"
            if prev_mid is not None:
                for pos in list(ledger.positions):
                    self.fills.close_market(ledger, pos, prev_mid, last_epoch,
                                            reason="forced:expired")

        return RunResult(
            outcome=state.outcome, detail=state.breach_detail, ticks=n,
            final_equity=ledger.equity(prev_mid if prev_mid else 0.0),
            peak_equity=state.peak_equity, trades=len(ledger.closed),
            trading_days=len(ledger.trading_days), ledger=ledger, state=state,
        )

    def _track_excursion(self, pos: Position, mid: float) -> None:
        mae, mfe = self._excursions.get(id(pos), (0.0, 0.0))
        move = (mid - pos.entry_price) * pos.direction
        self._excursions[id(pos)] = (min(mae, move), max(mfe, move))
