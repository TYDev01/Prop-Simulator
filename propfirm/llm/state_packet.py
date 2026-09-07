"""State-packet builder (BUILD_PROMPT §3, Layer 2 input).

Opus is candidate-triggered, never on the tick path. When the core proposes a
candidate, this assembles the compact, JSON-serialisable packet the model reasons
over: recent bars, the candidate's precomputed features, open-position state, and —
the part that actually matters under strict prop rules — the account's distance to
both loss floors, days elapsed, and progress to target.

Every number here is already visible to the deterministic core, so nothing the
model sees violates the no-lookahead guarantee (§5).
"""
from __future__ import annotations

from dataclasses import asdict

from propfirm.rules.breach import daily_loss_floor, max_loss_floor
from propfirm.sim.engine import Context
from propfirm.strategy.candidate import Candidate


def build_state_packet(ctx: Context, cand: Candidate, recent_bars: int = 20) -> dict:
    """Assemble the Opus input packet for one candidate decision."""
    equity = ctx.equity
    ledger = ctx.ledger
    state = ctx.state
    rules = state.rules
    phase = state.phase

    start_balance = state.start_balance
    target_equity = (start_balance * (1.0 + phase.profit_target_pct / 100.0)
                     if phase.profit_target_pct is not None else None)
    days_elapsed = (ctx.view.epoch - state.start_epoch) / 86400.0

    account = {
        "equity": round(equity, 2),
        "balance": round(ledger.balance, 2),
        "day_start_equity": round(ledger.day_start_equity, 2),
        "day_pnl": round(equity - ledger.day_start_equity, 2),
        # The two numbers a strict challenge lives or dies on.
        "room_to_daily_floor": round(equity - daily_loss_floor(state, ledger), 2),
        "room_to_max_floor": round(equity - max_loss_floor(state, ledger), 2),
        "days_elapsed": round(days_elapsed, 2),
        "days_limit": phase.max_days,
        "trading_days": len(ledger.trading_days),
        "min_trading_days": rules.min_trading_days,
        "progress_to_target": (round((equity - start_balance) /
                                     (target_equity - start_balance), 4)
                               if target_equity and target_equity > start_balance
                               else None),
        "phase": phase.name,
    }

    hist = ctx.view.history
    tail = hist[-recent_bars:] if len(hist) >= recent_bars else hist
    market = {
        "price": round(cand.features.get("price", ctx.mid), 4),
        "recent_prices": [round(float(p), 4) for p in tail],
    }

    position = None
    if ledger.positions:
        p = ledger.positions[0]
        position = {
            "direction": p.direction, "lots": p.lots,
            "entry_price": round(p.entry_price, 4),
            "floating": round(p.floating(ctx.engine.spec, ctx.mid), 2),
        }

    return {
        "candidate": {
            "direction": cand.direction,
            "lots": cand.lots,
            "sl": round(cand.sl, 4),
            "tp": round(cand.tp, 4),
            "stop_dist": round(cand.stop_dist, 4),
            "invalidation": round(cand.invalidation, 4),
            "risk_pct": cand.risk_pct,
            "features": {k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in cand.features.items()},
        },
        "account": account,
        "market": market,
        "position": position,
    }
