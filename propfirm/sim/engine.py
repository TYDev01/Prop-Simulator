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
            tag: str = "", meta: dict | None = None, trail_distance: float | None = None,
            breakeven_trigger: float | None = None, breakeven_offset: float = 0.0
            ) -> Position | None:
        return self.engine.fills.open_market(
            self.ledger, +1, lots, self.mid, self.view.epoch, sl, tp, tag, meta,
            trail_distance, breakeven_trigger, breakeven_offset)

    def sell(self, lots: float, sl: float | None = None, tp: float | None = None,
             tag: str = "", meta: dict | None = None, trail_distance: float | None = None,
             breakeven_trigger: float | None = None, breakeven_offset: float = 0.0
             ) -> Position | None:
        return self.engine.fills.open_market(
            self.ledger, -1, lots, self.mid, self.view.epoch, sl, tp, tag, meta,
            trail_distance, breakeven_trigger, breakeven_offset)

    def place(self, direction: int, lots: float, order_type, trigger: float,
              sl: float | None = None, tp: float | None = None, tag: str = "",
              meta: dict | None = None, expiry_epoch: int | None = None,
              trail_distance: float | None = None,
              breakeven_trigger: float | None = None, breakeven_offset: float = 0.0):
        """Queue a resting limit/stop entry; it fills when the market reaches trigger."""
        from propfirm.sim.orders import PendingOrder
        order = PendingOrder(
            direction=direction, lots=lots, order_type=order_type, trigger=trigger,
            sl=sl, tp=tp, tag=tag, meta=meta or {}, expiry_epoch=expiry_epoch,
            trail_distance=trail_distance, breakeven_trigger=breakeven_trigger,
            breakeven_offset=breakeven_offset)
        self.ledger.pending.append(order)
        return order

    def buy_limit(self, lots: float, trigger: float, **kw):
        from propfirm.sim.orders import OrderType
        return self.place(+1, lots, OrderType.LIMIT, trigger, **kw)

    def sell_limit(self, lots: float, trigger: float, **kw):
        from propfirm.sim.orders import OrderType
        return self.place(-1, lots, OrderType.LIMIT, trigger, **kw)

    def buy_stop(self, lots: float, trigger: float, **kw):
        from propfirm.sim.orders import OrderType
        return self.place(+1, lots, OrderType.STOP, trigger, **kw)

    def sell_stop(self, lots: float, trigger: float, **kw):
        from propfirm.sim.orders import OrderType
        return self.place(-1, lots, OrderType.STOP, trigger, **kw)

    def close(self, pos: Position, reason: str = "manual"):
        exc = self.engine._excursions.pop(id(pos), (0.0, 0.0))
        return self.engine.fills.close_market(self.ledger, pos, self.mid,
                                              self.view.epoch, reason, *exc)

    def close_partial(self, pos: Position, fraction: float, reason: str = "partial"):
        """Close a fraction of a position, leaving the rest open. Excursions persist."""
        mae, mfe = self.engine._excursions.get(id(pos), (0.0, 0.0))
        price = self.engine.fills.exit_price(pos.direction, self.mid)
        return self.ledger.close_partial(pos, fraction, price, self.view.epoch,
                                         reason, mae, mfe)


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
    elapsed_days: float = 0.0    # calendar days from first tick to the last processed
    equity_samples: list = field(default_factory=list)   # (epoch, equity), if sampled


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
            starting_balance: float | None = None,
            sample_every: int | None = None) -> RunResult:
        feed = TickFeed(ticks)
        ledger = Ledger(spec=self.spec,
                        starting_balance=starting_balance or self.rules.account_size)
        first_epoch = int(ticks["epoch"].iloc[0])
        state = ChallengeState(rules=self.rules, phase_index=phase_index,
                               start_epoch=first_epoch,
                               start_balance=ledger.starting_balance)
        self._excursions = {}

        equity_samples: list = []      # (epoch, equity), only when sample_every is set
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

            # Positions that existed before this tick's pending fills — only these
            # are managed/resolved now; a freshly-filled order waits until next tick
            # so it can never be stopped on its own opening tick.
            pre_positions = list(ledger.positions)

            # Fill any resting limit/stop entries the market reached this tick.
            if prev_mid is not None and ledger.pending:
                if self.fills.resolve_pending(ledger, prev_mid, mid, view.epoch):
                    equity = ledger.equity(mid)

            # Resolve stops/targets against the stop valid during this step, then
            # ratchet trailing / break-even for the NEXT tick. Managing before
            # resolving would apply a just-trailed stop retroactively to the step
            # and fire a false exit at the prior price.
            if prev_mid is not None and pre_positions:
                for pos in pre_positions:
                    self._track_excursion(pos, mid)
                    hit = self.fills.check_exit(pos, prev_mid, mid)
                    if hit is not None:
                        price, reason = hit
                        mae, mfe = self._excursions.pop(id(pos), (0.0, 0.0))
                        ledger.close(pos, price, view.epoch, reason, mae, mfe)
                    else:
                        self.fills.manage(pos, mid)   # update stop for next tick
                # A close moves money from floating to realised, so the equity
                # computed before the loop is stale.
                equity = ledger.equity(mid)

            # Broker margin stop-out: independent of the prop rules and resolved
            # after stops. Force-closes the worst position(s) until the margin
            # level recovers. Usually dormant -- prop rules breach first -- but
            # load-bearing for high-leverage configs (REMAINING.md §1.2).
            if ledger.positions and self._enforce_stop_out(ledger, mid, view.epoch):
                equity = ledger.equity(mid)

            # Hard kill switch, ahead of any strategy decision.
            outcome = check(state, ledger, mid, view.epoch, equity)
            if outcome is not Outcome.RUNNING:
                for pos in list(ledger.positions):
                    self.fills.close_market(ledger, pos, mid, view.epoch,
                                            reason=f"forced:{outcome.value}")
                break

            strategy.on_tick(Context(view=view, ledger=ledger, state=state, engine=self))
            if sample_every and n % sample_every == 0:
                equity_samples.append((view.epoch, ledger.equity(mid)))
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
            elapsed_days=(last_epoch - first_epoch) / 86400.0,
            equity_samples=equity_samples,
        )

    def _enforce_stop_out(self, ledger: Ledger, mid: float, epoch: int) -> bool:
        """Close the worst-loss positions until margin level >= the stop-out level.

        Mirrors the broker's own liquidation: worst floating loss first, rechecked
        after each close. Returns True if it closed anything.
        """
        level = self.spec.stop_out_level_pct
        closed_any = False
        while ledger.positions and ledger.margin_level(mid) < level:
            worst = min(ledger.positions, key=lambda p: p.floating(self.spec, mid))
            mae, mfe = self._excursions.pop(id(worst), (0.0, 0.0))
            self.fills.close_market(ledger, worst, mid, epoch,
                                    reason="stop_out", mae=mae, mfe=mfe)
            closed_any = True
        return closed_any

    def _track_excursion(self, pos: Position, mid: float) -> None:
        mae, mfe = self._excursions.get(id(pos), (0.0, 0.0))
        move = (mid - pos.entry_price) * pos.direction
        self._excursions[id(pos)] = (min(mae, move), max(mfe, move))
