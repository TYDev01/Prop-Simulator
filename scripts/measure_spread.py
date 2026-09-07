"""Phase 0 gate: measure real spread and fix the working timeframe by arithmetic.

Spread is the tax on every trade. Deriv's WebSocket tick feed carries no bid/ask,
so we read the broker's own quotes from a cached Deriv-Demo MT5 terminal.

The decision rule (spec section 4): pick the fastest timeframe at which spread is
a small enough fraction of the stop distance that cumulative cost over a full
challenge stays well inside the profit target.
"""
from __future__ import annotations

import asyncio
import sys

from propfirm.config import per_bar_sigma
from propfirm.data.deriv_client import DerivClient
from propfirm.data.mt5_ticks import find_tick_files, read_ticks
from propfirm.data.symbols import load_registry

TIMEFRAMES = [("M5", 300), ("M15", 900), ("H1", 3600), ("H4", 14400), ("D1", 86400)]

# Assumptions for the cost model, stated explicitly so they can be argued with.
STOP_SIGMAS = 1.5      # stop distance in per-bar sigmas
RISK_PCT = 1.0         # risk per trade, % of account
TRADES_PER_CHALLENGE = 200
PHASE1_TARGET_PCT = 8.0


async def live_price(symbol: str) -> float | None:
    try:
        async with DerivClient() as client:
            r = await client.send(
                {"ticks_history": symbol, "end": "latest", "count": 1, "style": "ticks"}
            )
        return float(r["history"]["prices"][-1])
    except Exception:
        return None


def cost_table(spread_pts: float, price: float, label: str) -> None:
    spread_frac = spread_pts / price
    print(f"\n  {label}: price={price:,.2f}  spread={spread_pts:g} pts"
          f"  = {spread_frac*100:.5f}% of price")
    print(f"  {'TF':<5}{'sigma/bar':>11}{'stop(1.5s)':>12}{'spread/stop':>13}"
          f"{'cost/trade':>12}{'cost/200':>11}{'% of 8%':>10}")
    for name, secs in TIMEFRAMES:
        sigma = per_bar_sigma(secs)
        stop = STOP_SIGMAS * sigma
        ratio = spread_frac / stop
        per_trade = ratio * RISK_PCT
        total = per_trade * TRADES_PER_CHALLENGE
        print(f"  {name:<5}{sigma*100:>10.3f}%{stop*100:>11.3f}%{ratio*100:>12.2f}%"
              f"{per_trade:>11.4f}%{total:>10.2f}%{total/PHASE1_TARGET_PCT*100:>9.0f}%")


def main() -> int:
    ticks = {tf.symbol: read_ticks(tf.path) for tf in find_tick_files()}
    ticks = {k: v for k, v in ticks.items() if not v.empty}
    if not ticks:
        print("no MT5 tick cache found; cannot measure spread", file=sys.stderr)
        return 1

    print("=== Measured broker spread (Deriv-Demo MT5 tick cache) ===")
    print(f"  {'symbol':<24}{'n':>6}{'mid':>13}{'spread':>10}{'min':>9}{'max':>9}{'% price':>10}")
    for sym, df in ticks.items():
        if "Volatility" not in sym and "Crash" not in sym and "Boom" not in sym:
            continue
        print(f"  {sym:<24}{len(df):>6}{df.mid.iloc[-1]:>13,.2f}{df.spread.median():>10.4f}"
              f"{df.spread.min():>9.4f}{df.spread.max():>9.4f}"
              f"{df.spread_frac.median()*100:>9.5f}%")

    v75 = ticks.get("Volatility 75 Index")
    if v75 is None:
        print("\nno Volatility 75 ticks; cannot close the gate", file=sys.stderr)
        return 1

    spread_pts = float(v75.spread.median())
    measured_px = float(v75.mid.iloc[-1])
    fixed = v75.spread.min() == v75.spread.max()

    print(f"\n  Vol75 spread is {'CONSTANT' if fixed else 'VARIABLE'} across "
          f"{len(v75)} ticks ({v75.timestamp.min():%Y-%m-%d %H:%M} -> "
          f"{v75.timestamp.max():%H:%M} UTC)")

    print("\n=== Cost model ===")
    print(f"  assumptions: stop={STOP_SIGMAS} sigma, risk={RISK_PCT}% per trade, "
          f"{TRADES_PER_CHALLENGE} trades/challenge, P1 target {PHASE1_TARGET_PCT}%")

    cost_table(spread_pts, measured_px, "AT MEASURED PRICE (2026-05)")

    live = asyncio.run(live_price(load_registry()["vol75"].symbol))
    if live:
        cost_table(spread_pts, live, "AT LIVE PRICE (today)")
        print(f"\n  NOTE: spread is fixed in POINTS, so its percentage cost moves"
              f" inversely with price.\n  Price {measured_px:,.0f} -> {live:,.0f}"
              f" ({live/measured_px:.2f}x) means cost per trade fell to"
              f" {measured_px/live*100:.0f}% of what it was.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
