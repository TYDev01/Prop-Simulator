"""Career-level payout ledger (spec section 6).

The point of this module is that a single passed challenge is not the result. The
result is what a *campaign* nets after fees, across attempts that mostly fail.

    fee -> phase 1 -> phase 2 -> funded -> payout cycles -> breach -> buy another

Reporting "I passed" without the fees spent on the attempts that didn't is the
central self-deception of retail prop trading, so net-after-fees is the headline
number here and gross payout is a supporting detail.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from propfirm.rules.breach import Outcome
from propfirm.rules.ruleset import FirmRules


@dataclass
class Payout:
    epoch: int
    account_profit: float
    trader_share: float


@dataclass
class Attempt:
    """One purchased challenge, followed as far as it got."""
    fee: float
    phase_outcomes: list[tuple[str, Outcome]] = field(default_factory=list)
    payouts: list[Payout] = field(default_factory=list)
    reached_funded: bool = False

    @property
    def gross_paid_out(self) -> float:
        return sum(p.trader_share for p in self.payouts)

    @property
    def net(self) -> float:
        return self.gross_paid_out - self.fee


@dataclass
class CareerLedger:
    rules: FirmRules
    attempts: list[Attempt] = field(default_factory=list)

    def start_attempt(self) -> Attempt:
        a = Attempt(fee=self.rules.challenge_fee)
        self.attempts.append(a)
        return a

    def record_payout(self, attempt: Attempt, epoch: int, account_profit: float) -> Payout:
        p = Payout(epoch=epoch, account_profit=account_profit,
                   trader_share=account_profit * self.rules.profit_split)
        attempt.payouts.append(p)
        return p

    # --- headline metrics ----------------------------------------------------

    @property
    def total_fees(self) -> float:
        return sum(a.fee for a in self.attempts)

    @property
    def total_payouts(self) -> float:
        return sum(a.gross_paid_out for a in self.attempts)

    @property
    def net(self) -> float:
        return self.total_payouts - self.total_fees

    def summary(self) -> dict:
        n = len(self.attempts)
        funded = sum(1 for a in self.attempts if a.reached_funded)
        paid = sum(1 for a in self.attempts if a.payouts)
        return {
            "attempts": n,
            "reached_funded": funded,
            "p_funded": funded / n if n else 0.0,
            "reached_payout": paid,
            "p_payout": paid / n if n else 0.0,
            "total_fees": self.total_fees,
            "total_payouts": self.total_payouts,
            "net": self.net,
            "net_per_attempt": self.net / n if n else 0.0,
        }
