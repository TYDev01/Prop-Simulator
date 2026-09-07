"""Reduced M.A.E. seed strategy (BUILD_PROMPT §7).

Deliberately simple, and built in full knowledge that on Vol75 it *cannot* have an
edge: §2.2 makes every entry rule zero-expectancy, so the honest prior is that this
matches random entry over enough trades. It is not meant to win. It is the seed the
research loop (Phase 4) evolves, and the thing the controls in §9.1 are there to
measure against.

Rayner Teo's M.A.E. is stripped to the three parts that survive on a structureless
synthetic (see §2.4 for why the rest are dropped, not ported):

  Structural stop placement -- the stop sits beyond a recent swing rather than at a
  fixed multiple. Kept purely as a *risk* tool: it ties stop distance to realised
  volatility, which is well-behaved at constant vol.

  Multi-filter selectivity -- a breakout must also agree with a slower bias filter
  and clear a cooldown. Kept as *cost control*: on a zero-edge instrument the only
  thing selectivity can do is cut trade count, and fewer trades pay less spread.

  Declared invalidation before entry -- the swing that defines the stop is the
  invalidation level, recorded on the position before it is opened.

Signals are computed only from *completed* bars; entries execute at the current
tick. The forming bar never informs a decision, so there is no lookahead.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from propfirm.sim.engine import Context
from propfirm.strategy.candidate import Candidate

# A decision hook gates a proposed candidate: it returns the candidate to open
# (possibly adjusted), or None to veto. This is the seam the Opus overlay plugs
# into; with no hook the core opens its own candidates unchanged (§3 dual-mode).
DecideHook = Callable[[Context, Candidate], "Candidate | None"]


@dataclass
class _Bar:
    o: float
    h: float
    l: float
    c: float


@dataclass
class ReducedMAE:
    """Breakout + bias + cooldown entries with swing-based structural stops.

    Every parameter is risk/selectivity geometry, never a claimed edge. Fully
    deterministic given the price path: the only randomness in a sweep is the data.
    """

    risk_pct: float = 1.0             # % of equity risked per trade
    rr: float = 2.0                   # reward-to-risk
    bar_seconds: int = 3600           # entry timeframe (H1, per Phase 0)
    donchian_lookback: int = 20       # breakout channel, in completed bars
    bias_lookback: int = 50           # slow mean the breakout must agree with
    stop_lookback: int = 10           # swing window that places the structural stop
    cooldown_bars: int = 3            # min bars between entries (selectivity)
    decide: DecideHook | None = None  # optional gate (the Opus overlay); None = core

    _bars: deque = field(init=False, repr=False)
    _bucket: int | None = field(default=None, init=False, repr=False)
    _cur: _Bar | None = field(default=None, init=False, repr=False)
    _bars_since_trade: int = field(default=10**9, init=False, repr=False)
    last_invalidation: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        cap = max(self.donchian_lookback, self.bias_lookback, self.stop_lookback) + 2
        self._bars = deque(maxlen=cap)

    # --- bar construction ----------------------------------------------------

    def on_tick(self, ctx: Context) -> None:
        price = ctx.mid
        bucket = ctx.view.epoch // self.bar_seconds

        if self._bucket is None:
            self._bucket, self._cur = bucket, _Bar(price, price, price, price)
            return
        if bucket == self._bucket:
            b = self._cur
            b.h = max(b.h, price)
            b.l = min(b.l, price)
            b.c = price
            return

        # A bar just completed. Push it, evaluate on it, then open the next bar.
        self._bars.append(self._cur)
        self._bars_since_trade += 1
        self._bucket, self._cur = bucket, _Bar(price, price, price, price)
        self._on_bar_close(ctx)

    # --- decision ------------------------------------------------------------

    def _on_bar_close(self, ctx: Context) -> None:
        need = max(self.donchian_lookback, self.bias_lookback, self.stop_lookback)
        if len(self._bars) < need:
            return
        if ctx.ledger.positions:                       # one position at a time
            return
        if self._bars_since_trade < self.cooldown_bars:
            return

        cand = self._propose(ctx)
        if cand is None:
            return

        # Core-only opens the candidate as-is. With an overlay, the decision hook
        # sees the candidate and may veto (None) or return an adjusted one.
        if self.decide is not None:
            cand = self.decide(ctx, cand)
            if cand is None:
                return

        self.last_invalidation = cand.invalidation
        opened = (ctx.buy if cand.direction > 0 else ctx.sell)(
            cand.lots, sl=cand.sl, tp=cand.tp, tag=cand.tag)
        if opened is not None:
            self._bars_since_trade = 0

    def _propose(self, ctx: Context) -> Candidate | None:
        """The pure candidate-detection logic: data in, a Candidate or None out."""
        bars = list(self._bars)
        price = ctx.mid

        upper = max(b.h for b in bars[-self.donchian_lookback:])
        lower = min(b.l for b in bars[-self.donchian_lookback:])
        sma = sum(b.c for b in bars[-self.bias_lookback:]) / self.bias_lookback

        # Breakout that also agrees with the slower bias. Both filters must clear;
        # this is selectivity (fewer trades), not a claim that breakouts predict.
        if price > upper and price > sma:
            direction = +1
            swing = min(b.l for b in bars[-self.stop_lookback:])
            stop_dist = price - swing
        elif price < lower and price < sma:
            direction = -1
            swing = max(b.h for b in bars[-self.stop_lookback:])
            stop_dist = swing - price
        else:
            return None
        if stop_dist <= 0:
            return None

        risk_amount = ctx.equity * self.risk_pct / 100.0
        # Never risk more than the room left to the daily loss floor.
        risk_amount = min(risk_amount, max(0.0, ctx.room_to_daily_loss() * 0.9))
        if risk_amount <= 0:
            return None

        lots = ctx.engine.spec.lots_for_risk(risk_amount, stop_dist)
        if lots < ctx.engine.spec.min_lot:
            return None

        entry = ctx.engine.fills.entry_price(direction, price)
        sl = swing                                     # structural stop = invalidation
        tp = entry + direction * stop_dist * self.rr
        return Candidate(
            direction=direction, lots=lots, entry=entry, sl=sl, tp=tp,
            stop_dist=stop_dist, invalidation=swing, risk_pct=self.risk_pct,
            features={
                "price": price, "donchian_upper": upper, "donchian_lower": lower,
                "bias_sma": sma, "breakout_pts": abs(price - (upper if direction > 0 else lower)),
                "stop_dist": stop_dist, "rr": self.rr,
            },
            tag=f"mae:inv={swing:.1f}",
        )


@dataclass(frozen=True)
class ReducedMAEFactory:
    """Picklable factory so Monte Carlo / campaign workers can build per trial.

    The strategy is deterministic, so `seed` is accepted for signature parity with
    the controls but only the data path it selects varies across trials -- which is
    exactly what makes 'seed strategy vs random entry on the same seeds' a clean
    A/B (§9.1).
    """

    risk_pct: float = 1.0
    rr: float = 2.0
    bar_seconds: int = 3600
    donchian_lookback: int = 20
    bias_lookback: int = 50
    stop_lookback: int = 10
    cooldown_bars: int = 3
    decide: DecideHook | None = None   # supply to run core+overlay; None = core-only

    def __call__(self, seed: int) -> ReducedMAE:
        return ReducedMAE(
            risk_pct=self.risk_pct, rr=self.rr, bar_seconds=self.bar_seconds,
            donchian_lookback=self.donchian_lookback,
            bias_lookback=self.bias_lookback, stop_lookback=self.stop_lookback,
            cooldown_bars=self.cooldown_bars, decide=self.decide,
        )
