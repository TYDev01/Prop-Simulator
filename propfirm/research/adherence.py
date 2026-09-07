"""Daily rule-adherence audit (BUILD_PROMPT §8).

The second research cadence, and a deliberately separate one: at each daily close the
question is *did the day's trading stay inside the rules and the declared risk
envelope* — never *should the strategy change*. Spec §8 keeps discipline and strategy
apart because mixing them corrupts both; this module is the discipline half.

Hard equity limits are already enforced live by the engine (they cannot be violated),
so this is not re-checking the kill switch. It audits, in **realised** terms, the
things a live account holder would review at the close: how much of each day's
loss-room was spent, whether any stop slipped well past 1R, how close the consistency
cap is, and whether the minimum-trading-days requirement is on track.

Day-start equity is reconstructed from the starting balance plus prior days' realised
P&L. Intraday floating is the engine's job (live breach detection); this is the
end-of-day ledger view.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from propfirm.research.trade_log import TradeRecord, record_from_trade
from propfirm.rules.ruleset import FirmRules
from propfirm.sim.ledger import Ledger, broker_day

# A stop that fills worse than this many R is worth flagging as a gap/slippage event.
STOP_SLIPPAGE_R = 1.5
# Warn when a day spends more than this share of its daily-loss room.
DAILY_ROOM_WARN = 0.8


@dataclass
class DayAudit:
    day: str
    n_trades: int
    day_pnl: float
    day_start_equity: float
    daily_loss_room: float           # currency distance from day-start to the floor
    daily_room_used: float           # 0..1 share of that room spent (realised)
    worst_trade_r: float
    flags: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.flags


@dataclass
class ChallengeAudit:
    days: list
    trading_days: int
    min_trading_days: int
    consistency_share: float | None   # best day as a share of total profit
    consistency_cap: float | None
    flags: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.flags and all(d.ok for d in self.days)


def audit_from_ledger(ledger: Ledger, rules: FirmRules,
                      start_balance: float | None = None) -> ChallengeAudit:
    """Audit a finished (or in-progress) challenge for rule adherence, day by day."""
    start_balance = start_balance if start_balance is not None else ledger.starting_balance
    records = [record_from_trade(t) for t in ledger.closed]

    by_day: dict[str, list[TradeRecord]] = {}
    for r in records:
        by_day.setdefault(broker_day(r.closed_epoch, rules.broker_utc_offset_hours), []
                          ).append(r)

    day_audits: list[DayAudit] = []
    running = start_balance                       # equity at the start of each day
    daily_frac = rules.max_daily_loss_pct / 100.0
    for day in sorted(by_day):
        recs = by_day[day]
        day_pnl = sum(r.pnl for r in recs)
        room = running * daily_frac
        used = max(0.0, -day_pnl) / room if room > 0 else 0.0
        worst_r = min((r.r_multiple for r in recs), default=0.0)

        flags = []
        if used >= DAILY_ROOM_WARN:
            flags.append(f"spent {used * 100:.0f}% of the daily-loss room")
        if worst_r < -STOP_SLIPPAGE_R:
            flags.append(f"a stop slipped to {worst_r:.2f}R (gap past the level)")
        day_audits.append(DayAudit(
            day=day, n_trades=len(recs), day_pnl=round(day_pnl, 2),
            day_start_equity=round(running, 2), daily_loss_room=round(room, 2),
            daily_room_used=round(used, 4), worst_trade_r=round(worst_r, 3),
            flags=flags))
        running += day_pnl                        # next day starts here (realised)

    # Challenge-level checks: consistency cap and minimum trading days.
    positive_days = {d.day: d.day_pnl for d in day_audits if d.day_pnl > 0}
    total_profit = sum(positive_days.values())
    consistency_share = (max(positive_days.values()) / total_profit
                         if positive_days and total_profit > 0 else None)
    flags = []
    cap = rules.consistency_max_day_pct
    if cap is not None and consistency_share is not None and consistency_share * 100 > cap:
        flags.append(f"best day is {consistency_share * 100:.0f}% of profit (cap {cap:.0f}%)")

    trading_days = len(ledger.trading_days)
    if trading_days < rules.min_trading_days:
        flags.append(f"{trading_days}/{rules.min_trading_days} trading days so far")

    return ChallengeAudit(
        days=day_audits, trading_days=trading_days,
        min_trading_days=rules.min_trading_days,
        consistency_share=consistency_share, consistency_cap=cap, flags=flags)


def summarise_audit(audit: ChallengeAudit) -> dict:
    return {
        "days": len(audit.days),
        "clean_days": sum(1 for d in audit.days if d.ok),
        "flagged_days": sum(1 for d in audit.days if not d.ok),
        "trading_days": audit.trading_days,
        "min_trading_days_met": audit.trading_days >= audit.min_trading_days,
        "consistency_share": (round(audit.consistency_share, 4)
                              if audit.consistency_share is not None else None),
        "adherent": audit.ok,
        "flags": audit.flags,
    }
