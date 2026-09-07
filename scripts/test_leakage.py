"""Spec gate (section 5): prove the strategy cannot see the future.

Method: poison every tick after the cursor with an unmistakable sentinel, replay
the whole feed, and assert the sentinel never appears in anything handed out. A
backtest that fails this produces results that are not merely optimistic but
meaningless, and it fails silently -- hence a dedicated gate.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from propfirm.sim.feed import LookaheadError, TickFeed

SENTINEL = 1.0e12


def main() -> int:
    n = 5_000
    rng = np.random.default_rng(0)
    real = 50_000 + np.cumsum(rng.normal(0, 5, n))
    df = pd.DataFrame({"epoch": np.arange(n), "price": real})

    failures: list[str] = []

    # --- 1. Sentinel poisoning -------------------------------------------------
    # Rebuild the feed at each step with the future replaced by SENTINEL. If any
    # view reaches past the cursor, the sentinel shows up immediately.
    checks = 0
    for cut in range(1, n, 97):
        poisoned = real.copy()
        poisoned[cut:] = SENTINEL
        feed = TickFeed(pd.DataFrame({"epoch": df.epoch, "price": poisoned}))
        for _ in range(cut):
            view = feed.step()
            if view is None:
                break
            checks += 1
            if view.price >= SENTINEL or (view.history >= SENTINEL).any():
                failures.append(f"sentinel leaked at cut={cut} epoch={view.epoch}")
                break
    print(f"1. sentinel poisoning: {checks:,} views inspected -> "
          f"{'LEAK' if failures else 'clean'}")

    # --- 2. History must end exactly at the cursor -----------------------------
    feed = TickFeed(df)
    bad = 0
    while True:
        view = feed.step()
        if view is None:
            break
        if view.history[-1] != view.price:
            bad += 1
        if len(view.history) != min(feed.position + 1, 20_000):
            bad += 1
    print(f"2. history ends at cursor: {'FAIL' if bad else 'PASS'} ({bad} anomalies)")
    if bad:
        failures.append("history/cursor mismatch")

    # --- 3. Views must be immutable -------------------------------------------
    feed = TickFeed(df)
    view = feed.step()
    try:
        view.history[0] = 123.0
        failures.append("view history is writable")
        print("3. view immutability: FAIL (writable)")
    except ValueError:
        print("3. view immutability: PASS")

    # --- 4. The unsafe accessor is the only route forward, and is explicit -----
    feed = TickFeed(df)
    feed.step()
    nxt = feed.peek_future()
    ok = nxt == real[1]
    print(f"4. peek_future is test-only and correct: {'PASS' if ok else 'FAIL'}")
    if not ok:
        failures.append("peek_future wrong")

    print("\n" + "=" * 52)
    if failures:
        print("LEAKAGE GATE: FAIL")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("LEAKAGE GATE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
