"""Account ledger: balance, equity, margin, and the anchors prop rules watch.

Two anchors matter more than anything else here and both are easy to get subtly
wrong:

  day_start_equity  Reset at the broker's daily boundary (00:00 GMT+2 by default).
                    The max-daily-loss rule measures against this, not against
                    a rolling 24h window.

  equity_hwm        High-water-mark of EQUITY, not balance. The strict ruleset in
                    spec section 6 trails max drawdown off this, which means
                    floating profit ratchets the limit up and floating loss counts
                    against you immediately. Tracking balance instead would make
                    the account look safer than it is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from propfirm.sim.contract import ContractSpec

# Deriv/most prop firms roll the trading day at 00:00 GMT+2.
BROKER_UTC_OFFSET_HOURS = 2


@dataclass
class Position:
    direction: int               # +1 long, -1 short
    lots: float
    entry_price: float
    opened_epoch: int
    sl: float | None = None
    tp: float | None = None
    tag: str = ""

    def floating(self, spec: ContractSpec, mark: float) -> float:
        return spec.pnl(self.direction, self.lots, self.entry_price, mark)


@dataclass
class ClosedTrade:
    direction: int
    lots: float
    entry_price: float
    exit_price: float
    opened_epoch: int
    closed_epoch: int
    pnl: float
    reason: str
    mae: float = 0.0             # worst adverse excursion while open, in price
    mfe: float = 0.0             # best favourable excursion while open, in price
    tag: str = ""


def broker_day(epoch: int, offset_hours: int = BROKER_UTC_OFFSET_HOURS) -> str:
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc) + timedelta(hours=offset_hours)
    return dt.strftime("%Y-%m-%d")


def broker_day_bounds(epoch: int, offset_hours: int = BROKER_UTC_OFFSET_HOURS
                      ) -> tuple[int, int]:
    """[start, end) UTC epochs of the broker day containing `epoch`.

    Pure integer arithmetic: formatting a date string per tick was ~18% of runtime.
    """
    off = offset_hours * 3600
    start = ((epoch + off) // 86400) * 86400 - off
    return start, start + 86400


@dataclass
class Ledger:
    spec: ContractSpec
    starting_balance: float
    balance: float = 0.0
    positions: list[Position] = field(default_factory=list)
    closed: list[ClosedTrade] = field(default_factory=list)

    day_start_equity: float = 0.0
    current_day: str = ""
    equity_hwm: float = 0.0
    trading_days: set[str] = field(default_factory=set)
    daily_pnl: dict[str, float] = field(default_factory=dict)
    _day_end_epoch: int = 0

    def __post_init__(self) -> None:
        if self.balance == 0.0:
            self.balance = self.starting_balance
        self.day_start_equity = self.balance
        self.equity_hwm = self.balance

    # --- valuation -----------------------------------------------------------

    def floating(self, mark: float) -> float:
        return sum(p.floating(self.spec, mark) for p in self.positions)

    def equity(self, mark: float) -> float:
        return self.balance + self.floating(mark)

    def margin_used(self, mark: float) -> float:
        return sum(self.spec.margin_required(p.lots, mark) for p in self.positions)

    def free_margin(self, mark: float) -> float:
        return self.equity(mark) - self.margin_used(mark)

    def margin_level(self, mark: float) -> float:
        used = self.margin_used(mark)
        return float("inf") if used <= 0 else self.equity(mark) / used * 100.0

    # --- day boundary --------------------------------------------------------

    def mark(self, epoch: int, mark_price: float,
             equity: float | None = None) -> float:
        """Advance clock-dependent state. Call once per tick, before rule checks.

        Returns the equity it computed so callers need not recompute it.
        """
        eq = self.equity(mark_price) if equity is None else equity
        if epoch >= self._day_end_epoch:
            start, end = broker_day_bounds(epoch, BROKER_UTC_OFFSET_HOURS)
            self._day_end_epoch = end
            day = broker_day(epoch)
            if day != self.current_day:
                self.current_day = day
                self.day_start_equity = eq
                self.daily_pnl.setdefault(day, 0.0)
        if eq > self.equity_hwm:
            self.equity_hwm = eq
        return eq

    def day_pnl(self, mark: float) -> float:
        return self.equity(mark) - self.day_start_equity

    # --- position lifecycle --------------------------------------------------

    def open(self, position: Position) -> None:
        self.positions.append(position)
        self.trading_days.add(broker_day(position.opened_epoch))

    def close(self, position: Position, exit_price: float, epoch: int,
              reason: str, mae: float = 0.0, mfe: float = 0.0) -> ClosedTrade:
        pnl = self.spec.pnl(position.direction, position.lots,
                            position.entry_price, exit_price)
        self.balance += pnl
        self.positions.remove(position)
        day = broker_day(epoch)
        self.daily_pnl[day] = self.daily_pnl.get(day, 0.0) + pnl
        trade = ClosedTrade(
            direction=position.direction, lots=position.lots,
            entry_price=position.entry_price, exit_price=exit_price,
            opened_epoch=position.opened_epoch, closed_epoch=epoch,
            pnl=pnl, reason=reason, mae=mae, mfe=mfe, tag=position.tag,
        )
        self.closed.append(trade)
        return trade
