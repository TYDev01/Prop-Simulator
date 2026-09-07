"""Per-trade research log (BUILD_PROMPT §8).

The first cadence of the research loop is per-trade: record the feature vector at
entry, the decision and its declared invalidation, the outcome, and MAE/MFE. The
engine already computes MAE/MFE and carries entry metadata on each closed trade;
this turns that into a durable, analysable record — the dataset every later cadence
(daily adherence, hypothesis tests, calibration) reads from.

Kept deliberately plain: one JSON-serialisable record per trade, appended to JSONL.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from propfirm.sim.ledger import ClosedTrade, Ledger


@dataclass
class TradeRecord:
    opened_epoch: int
    closed_epoch: int
    direction: int
    lots: float
    entry_price: float
    exit_price: float
    pnl: float
    reason: str                  # sl / tp / manual / forced:* / stop_out
    mae: float                   # worst adverse excursion while open (price)
    mfe: float                   # best favourable excursion while open (price)
    r_multiple: float            # pnl in units of the risked amount, if derivable
    tag: str = ""
    meta: dict = field(default_factory=dict)   # features, invalidation, overlay notes

    @property
    def win(self) -> bool:
        return self.pnl > 0


def _r_multiple(t: ClosedTrade) -> float:
    """Realised P&L in R (risk units), from the stop distance recorded at entry.

    The stop distance is in price; risk in currency is stop_dist * lots * size,
    but pnl already includes contract size, so pnl / (stop_dist * lots) is the R
    multiple. Returns 0.0 when the stop distance wasn't recorded.
    """
    stop_dist = t.meta.get("stop_dist")
    if not stop_dist or not t.lots:
        return 0.0
    # pnl = dir*(exit-entry)*lots*size; risk_at_stop = stop_dist*lots*size.
    # size cancels, so R = pnl / (stop_dist * lots) with size folded into pnl.
    from math import isfinite
    denom = stop_dist * t.lots
    r = t.pnl / denom if denom else 0.0
    return r if isfinite(r) else 0.0


def record_from_trade(t: ClosedTrade) -> TradeRecord:
    return TradeRecord(
        opened_epoch=t.opened_epoch, closed_epoch=t.closed_epoch,
        direction=t.direction, lots=t.lots, entry_price=t.entry_price,
        exit_price=t.exit_price, pnl=t.pnl, reason=t.reason,
        mae=t.mae, mfe=t.mfe, r_multiple=_r_multiple(t), tag=t.tag,
        meta=dict(t.meta),
    )


def records_from_ledger(ledger: Ledger) -> list[TradeRecord]:
    return [record_from_trade(t) for t in ledger.closed]


def write_jsonl(records: list[TradeRecord], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        for r in records:
            fh.write(json.dumps(asdict(r)) + "\n")


def summarise(records: list[TradeRecord]) -> dict:
    """Descriptive stats over a trade log. Not a verdict — that is the loop's job."""
    n = len(records)
    if n == 0:
        return {"n": 0, "win_rate": 0.0, "expectancy": 0.0, "total_pnl": 0.0,
                "mean_r": 0.0, "mean_mae": 0.0, "mean_mfe": 0.0}
    wins = sum(1 for r in records if r.win)
    total = sum(r.pnl for r in records)
    return {
        "n": n,
        "win_rate": wins / n,
        "expectancy": total / n,
        "total_pnl": total,
        "mean_r": sum(r.r_multiple for r in records) / n,
        "mean_mae": sum(r.mae for r in records) / n,
        "mean_mfe": sum(r.mfe for r in records) / n,
    }
