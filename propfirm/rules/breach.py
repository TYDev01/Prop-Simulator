"""Live breach detection.

Checked every tick against equity, not at bar close and not against balance. A
challenge dies on floating drawdown, so evaluating only realised P&L would let the
simulator survive accounts that a real firm would have killed.

The kill switch here is unconditional -- spec section 3 requires hard limits to be
`if` statements that no model output can reach.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from propfirm.rules.ruleset import FirmRules, PhaseRules, TrailOn
from propfirm.sim.ledger import Ledger, broker_day


class Outcome(str, Enum):
    RUNNING = "running"
    PASSED = "passed"
    BREACHED_DAILY = "breached_daily_loss"
    BREACHED_TOTAL = "breached_max_loss"
    EXPIRED = "expired_time_limit"


@dataclass
class ChallengeState:
    rules: FirmRules
    phase_index: int
    start_epoch: int
    start_balance: float
    outcome: Outcome = Outcome.RUNNING
    breach_epoch: int | None = None
    breach_detail: str = ""
    peak_equity: float = 0.0
    # Reasons a target was reached but the phase could not yet be passed.
    blocked_by: list[str] = field(default_factory=list)

    @property
    def phase(self) -> PhaseRules:
        return self.rules.phase(self.phase_index)

    @property
    def finished(self) -> bool:
        return self.outcome is not Outcome.RUNNING


def max_loss_floor(state: ChallengeState, ledger: Ledger) -> float:
    """Equity level at which the max-loss rule triggers."""
    frac = state.rules.max_total_loss_pct / 100.0
    if state.rules.trail_on is TrailOn.EQUITY:
        return ledger.equity_hwm * (1.0 - frac)
    if state.rules.trail_on is TrailOn.BALANCE:
        return max(ledger.balance, state.start_balance) * (1.0 - frac)
    return state.start_balance * (1.0 - frac)


def daily_loss_floor(state: ChallengeState, ledger: Ledger) -> float:
    frac = state.rules.max_daily_loss_pct / 100.0
    return ledger.day_start_equity * (1.0 - frac)


def consistency_ok(state: ChallengeState, ledger: Ledger, equity: float
                   ) -> tuple[bool, str]:
    cap = state.rules.consistency_max_day_pct
    if cap is None:
        return True, ""
    total_profit = equity - state.start_balance
    if total_profit <= 0:
        return True, ""
    best_day = max(ledger.daily_pnl.values(), default=0.0)
    share = best_day / total_profit * 100.0
    if share > cap:
        return False, f"best day is {share:.1f}% of profit (cap {cap:.0f}%)"
    return True, ""


def check(state: ChallengeState, ledger: Ledger, mark: float, epoch: int,
          equity: float | None = None) -> Outcome:
    """Evaluate all rules for the current tick. Order matters: losses first."""
    if state.finished:
        return state.outcome

    if equity is None:
        equity = ledger.equity(mark)
    state.peak_equity = max(state.peak_equity, equity)

    # 1. Daily loss.
    floor_d = daily_loss_floor(state, ledger)
    if equity <= floor_d:
        state.outcome = Outcome.BREACHED_DAILY
        state.breach_epoch = epoch
        state.breach_detail = (f"equity {equity:,.2f} <= daily floor {floor_d:,.2f} "
                               f"(day start {ledger.day_start_equity:,.2f})")
        return state.outcome

    # 2. Max total loss.
    floor_t = max_loss_floor(state, ledger)
    if equity <= floor_t:
        state.outcome = Outcome.BREACHED_TOTAL
        state.breach_epoch = epoch
        state.breach_detail = (f"equity {equity:,.2f} <= max-loss floor {floor_t:,.2f} "
                               f"(hwm {ledger.equity_hwm:,.2f})")
        return state.outcome

    # 3. Time limit.
    if state.phase.max_days is not None:
        days = (epoch - state.start_epoch) / 86400.0
        if days > state.phase.max_days:
            state.outcome = Outcome.EXPIRED
            state.breach_epoch = epoch
            state.breach_detail = f"{days:.1f}d > {state.phase.max_days}d limit"
            return state.outcome

    # 4. Target reached -- but only passes if the gating rules are satisfied.
    target = state.phase.profit_target_pct
    if target is not None:
        goal = state.start_balance * (1.0 + target / 100.0)
        if equity >= goal:
            blocked = []
            n_days = len(ledger.trading_days)
            if n_days < state.rules.min_trading_days:
                blocked.append(f"{n_days}/{state.rules.min_trading_days} trading days")
            ok, why = consistency_ok(state, ledger, equity)
            if not ok:
                blocked.append(why)
            # A target hit on floating profit is not banked until flat.
            if ledger.positions:
                blocked.append(f"{len(ledger.positions)} position(s) still open")
            state.blocked_by = blocked
            if not blocked:
                state.outcome = Outcome.PASSED
                state.breach_epoch = epoch
                return state.outcome

    return Outcome.RUNNING
