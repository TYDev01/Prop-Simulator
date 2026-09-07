# Strategy version history

Per BUILD_PROMPT §8, every strategy change is versioned with its rationale, the
evidence behind it, and the test result — adopted or not. The research loop
(`propfirm/research/champion_challenger.py`) owns the promotion decision; this file
is the human-readable log.

The honest backdrop (§2.2): Volatility 75 is driftless, so **no version can create
edge**. Versions only move the risk geometry — risk per trade, trade frequency, and
how a trade is managed — which is the one lever §2.3 says is real. A version that
does not beat the incumbent out-of-sample is not adopted.

---

## v1 — reduced M.A.E. (`ReducedMAEFactory()`)

The seed. Donchian breakout (20 bars) that must agree with a 50-bar bias mean, a
10-bar swing structural stop, 3-bar cooldown, 1% risk, 1:2 RR, market entry with a
static stop and target.

**Finding:** heavily selective — ~4–5 trades per 30-day challenge. That trades
drawdown risk for **deadline risk**: most accounts expire without hitting the target
rather than breaching. P(pass) sits *below* random entry, exactly as the zero-edge
result predicts for information-free entries.

## v2 — managed reduced M.A.E. (`seed_v2_factory()`)

**Rationale (built from v1's findings):** loosen selectivity to fund the
minimum-trading-days requirement and stop dying of the deadline (donchian 20→12,
bias 50→40, cooldown 3→1), and manage each trade with the new order model so a
runner that reverses doesn't give the whole move back — break-even at +1R, a half
partial at +1R, and a 2R trailing stop.

**Evidence (out-of-sample, champion/challenger on validation seeds, 10s ticks):**

| | P(pass) |
|---|---|
| v1 (incumbent) | 0.0% |
| v2 (challenger) | 0.0% |
| promotion margin | +3.0 pts |

**Result: REJECTED.** v2 trades more (≈9 vs ≈5) and its management reshapes the P&L
distribution (more small partial wins, break-even scratches), but it does **not**
beat v1 out-of-sample, so the loop did not promote it. More trades on a zero-edge
instrument means more spread paid, which cancels the distribution-shaping benefit.

This is the anti-overfitting machinery working as designed: an advancement was
proposed from real findings, tested on held-out seeds, and declined because it failed
to clear the bar. The next genuine lever to explore is not a better entry but the
**risk-geometry surface** (the 1% interior optimum) and the **career economics** —
whether any configuration clears the $500 fee, which the campaign says it does not.

> Numbers above are at 10s replay resolution (biased pessimistically vs the 2s
> baseline) and inherit the UNVERIFIED contract fields (REMAINING §3.1); the *ranking*
> is what the decision rests on, and it is stable.
