"""The Opus overlay and the dual-mode switch (BUILD_PROMPT §3).

The overlay turns a `DecisionProvider` into a `decide` hook for the seed strategy:
on each candidate it builds a state packet, asks the provider, and translates the
`Decision` back into a candidate to open (possibly with tightened risk) or a veto.
Hard risk limits stay in Python — the provider may only *reduce* risk, never raise
it past what the core sized (§3: the kill switch is unreachable by any model output).

`dual_mode` returns the core-only and core+overlay strategy factories from one
config, so both arms run on identical data and detection logic and differ solely in
whether the overlay gates the trade — the §3 requirement that makes "does the
overlay beat the core?" an answerable question.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from propfirm.llm.provider import DecisionProvider
from propfirm.llm.state_packet import build_state_packet
from propfirm.sim.engine import Context
from propfirm.strategy.candidate import Candidate
from propfirm.strategy.seed_mae import ReducedMAE, ReducedMAEFactory


@dataclass
class OpusOverlay:
    """A candidate gate backed by a decision provider."""
    provider: DecisionProvider
    recent_bars: int = 20

    def __call__(self, ctx: Context, cand: Candidate) -> Candidate | None:
        packet = build_state_packet(ctx, cand, recent_bars=self.recent_bars)
        decision = self.provider.decide(packet)
        if not decision.is_trade:                # veto or malformed ⇒ no trade
            return None

        lots, sl, tp = cand.lots, cand.sl, cand.tp

        # Risk may only be tightened. A model asking for more risk than the core
        # sized is clamped to the core's risk, never honoured upward.
        if decision.risk_pct is not None and decision.risk_pct < cand.risk_pct:
            scale = max(0.0, decision.risk_pct) / cand.risk_pct
            lots = cand.lots * scale
            if lots < ctx.engine.spec.min_lot:
                return None

        # Stop/target overrides are accepted only when they don't widen risk: a
        # long's stop may move up (closer), a short's down; targets are free.
        if decision.sl is not None:
            if (cand.direction > 0 and decision.sl >= cand.sl) or \
               (cand.direction < 0 and decision.sl <= cand.sl):
                sl = decision.sl
        if decision.tp is not None:
            tp = decision.tp

        return Candidate(direction=cand.direction, lots=lots, entry=cand.entry,
                         sl=sl, tp=tp, stop_dist=cand.stop_dist,
                         invalidation=cand.invalidation, risk_pct=cand.risk_pct,
                         features=cand.features, tag=cand.tag)


def dual_mode(provider: DecisionProvider, **seed_params
              ) -> tuple[ReducedMAEFactory, Callable[[int], ReducedMAE]]:
    """Return (core_factory, overlay_factory) sharing one seed configuration.

    core_factory runs the deterministic seed alone; overlay_factory runs the same
    seed with the Opus overlay gating every candidate. Feed both the same seeds.
    """
    core = ReducedMAEFactory(**seed_params)
    overlay = OpusOverlay(provider)
    overlay_factory = ReducedMAEFactory(decide=overlay, **seed_params)
    return core, overlay_factory
