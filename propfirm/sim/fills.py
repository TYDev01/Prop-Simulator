"""Fill engine: quotes, order execution, and stop/target resolution.

Spec section 5 forbids bar-level fills. Two things it insists on:

  Intra-bar ordering. On a 75%-vol instrument, whether the stop or the target was
  touched first inside a bar decides a large share of the result. Only a tick-level
  walk can answer it, so this engine is driven one tick at a time.

  Honest gaps. A stop is a trigger, not a guaranteed price. When the market jumps
  past the level, the fill happens at the price actually available, not at the level
  requested. Modelling stops as exact fills is the single largest source of fake
  backtest profit -- catastrophically so on Boom/Crash, where spikes gap straight
  through stops by design.
"""
from __future__ import annotations

from dataclasses import dataclass

from propfirm.sim.contract import ContractSpec
from propfirm.sim.ledger import Ledger, Position


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


def quote_from_mid(mid: float, spec: ContractSpec) -> Quote:
    half = spec.spread_points * spec.point / 2.0
    return Quote(bid=mid - half, ask=mid + half)


@dataclass
class FillEngine:
    spec: ContractSpec
    slippage_points: float = 0.0     # extra adverse points on market/stop fills

    # --- quoting -------------------------------------------------------------

    def quote(self, mid: float) -> Quote:
        return quote_from_mid(mid, self.spec)

    def entry_price(self, direction: int, mid: float) -> float:
        """Buy lifts the ask, sell hits the bid; slippage is always adverse."""
        q = self.quote(mid)
        slip = self.slippage_points * self.spec.point
        return (q.ask + slip) if direction > 0 else (q.bid - slip)

    def exit_price(self, direction: int, mid: float) -> float:
        q = self.quote(mid)
        slip = self.slippage_points * self.spec.point
        return (q.bid - slip) if direction > 0 else (q.ask + slip)

    # --- stop / target resolution -------------------------------------------

    def check_exit(self, pos: Position, prev_mid: float, mid: float
                   ) -> tuple[float, str] | None:
        """Resolve SL/TP for one tick step. Returns (fill_price, reason) or None.

        The fill price is the WORSE of the requested level and the price actually
        available, so a jump through the level is charged at the gap, not the level.
        When a single tick step brackets both SL and TP, the stop is taken first:
        pessimistic, and the honest reading when tick order inside the jump is
        unknowable.
        """
        exit_now = self.exit_price(pos.direction, mid)
        exit_prev = self.exit_price(pos.direction, prev_mid)
        lo, hi = min(exit_prev, exit_now), max(exit_prev, exit_now)

        hit_sl = pos.sl is not None and lo <= pos.sl <= hi
        hit_tp = pos.tp is not None and lo <= pos.tp <= hi

        # Also catch a gap that leapt clean over a level without bracketing it.
        if pos.sl is not None and not hit_sl:
            hit_sl = (exit_now <= pos.sl) if pos.direction > 0 else (exit_now >= pos.sl)
        if pos.tp is not None and not hit_tp:
            hit_tp = (exit_now >= pos.tp) if pos.direction > 0 else (exit_now <= pos.tp)

        if hit_sl:
            # Adverse gap: fill at the worse of level and available price.
            fill = min(pos.sl, exit_now) if pos.direction > 0 else max(pos.sl, exit_now)
            return fill, "sl"
        if hit_tp:
            # Favourable gap does NOT pay better than the resting order.
            fill = pos.tp
            return fill, "tp"
        return None

    # --- orders --------------------------------------------------------------

    def open_market(self, ledger: Ledger, direction: int, lots: float, mid: float,
                    epoch: int, sl: float | None = None, tp: float | None = None,
                    tag: str = "", meta: dict | None = None) -> Position | None:
        """Open at market. Rejects orders that violate the broker's stops level."""
        price = self.entry_price(direction, mid)
        min_dist = self.spec.stops_level_points * self.spec.point
        if sl is not None and abs(price - sl) < min_dist:
            return None
        if tp is not None and abs(price - tp) < min_dist:
            return None
        if lots < self.spec.min_lot:
            return None
        if ledger.free_margin(mid) < self.spec.margin_required(lots, mid):
            return None

        pos = Position(direction=direction, lots=lots, entry_price=price,
                       opened_epoch=epoch, sl=sl, tp=tp, tag=tag,
                       meta=meta or {})
        ledger.open(pos)
        return pos

    def close_market(self, ledger: Ledger, pos: Position, mid: float, epoch: int,
                     reason: str = "manual", mae: float = 0.0, mfe: float = 0.0):
        return ledger.close(pos, self.exit_price(pos.direction, mid), epoch,
                            reason, mae, mfe)
