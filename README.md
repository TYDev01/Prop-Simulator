# Prop Simulator

A tick-accurate simulator of a strict prop-firm challenge on Deriv synthetic
indices, built to answer one question honestly:

> **What is the probability of passing, and does it clear the challenge fee?**

The deliverable is not a profitable bot. It is a measuring instrument precise
enough to prove or disprove an edge, and an audit trail of how a strategy changed
and why.

- Primary instrument: **Volatility 75 Index** (`R_75`)
- Later: **Boom 1000**, **Crash 1000**
- Python owns data, execution, and risk enforcement; Claude Opus 5 owns judgement
  and strategy evolution *(Phase 3, not yet built)*

Full specification: [`BUILD_PROMPT.md`](BUILD_PROMPT.md).
Outstanding work: [`REMAINING.md`](REMAINING.md).

---

## The constraint everything else follows from

Volatility 75 is a **driftless random walk at constant 75% annualised
volatility** — a published, audited RNG with no participants, no order flow, and no
news. For a driftless walk with a stop at distance `a` and a target at distance `b`:

```
P(target first) = a / (a + b)
```

At 1:2 reward-to-risk the win rate is **exactly 1/3**, so expectancy is
`(1/3)(2R) − (2/3)(1R) = 0`. At 1:3 it is exactly 1/4. **Every** reward-to-risk
ratio gives exactly zero expectancy — the win rate self-adjusts to cancel the
payoff. Add spread and every configuration is strictly negative.

This is not pessimism; it is the terrain. It means the only real degrees of freedom
are **risk per trade, trade frequency, and how they interact with the drawdown
limits** — and that is a small, clean space the simulator can search exhaustively.

It also means structural price-action reasoning does not transfer. Support and
resistance is justified by trapped traders exiting at breakeven; there are no
trapped traders. Accumulation and distribution describe institutions; there are no
institutions.

---

## What has been measured

### Broker spread: 4.50 index points, constant

Deriv's WebSocket feed carries a single price with no bid/ask, so spread was
recovered by reverse-engineering a cached MetaTrader 5 tick file
(`propfirm/data/mt5_ticks.py`). Spread cost over a 200-trade challenge, as a share
of the 8% Phase 1 target:

| Timeframe | @ price 28,279 | @ price 49,787 |
|---|---|---|
| M5 | **115%** | 65% |
| M15 | 66% | 38% |
| **H1** *(chosen)* | **33%** | **19%** |
| H4 | 17% | 9% |

M5 is disqualified — spread alone costs more than the entire profit target. The
working timeframe was fixed at **H1** by this arithmetic, not by preference.

Spread is fixed in **points**, so its percentage cost moves inversely with an index
price that is itself a random walk. A configuration viable at 50,000 can become
unviable at 28,000 with no change in strategy.

### P(pass): 26.5% at best, random entry, strict rules

200 trials per configuration, 30-day challenges, real 2s tick rate:

| risk% | RR | trades/day | **P(pass)** | breached | expired |
|---|---|---|---|---|---|
| 0.5 | 2.0 | 3 | 17.0% | 41 | **125** |
| **1.0** | **2.0** | **3** | **26.5%** | 124 | 23 |
| 2.0 | 2.0 | 3 | 10.0% | 180 | 0 |
| 3.0 | 2.0 | 3 | 3.0% | 194 | 0 |
| 1.0 | 1.0 | 3 | 24.5% | 109 | 42 |
| 1.0 | 3.0 | 3 | 23.0% | 148 | 6 |
| 1.0 | 2.0 | 1 | 14.5% | 70 | **101** |
| 1.0 | 2.0 | 8 | 21.0% | 157 | 1 |

Three things this shows:

1. **Risk per trade is the dominant lever** — 3% to 26.5% across the range, with a
   genuine interior optimum at 1%.
2. **Reward-to-risk barely matters** — 23% to 26.5% across a 3× range, exactly as
   the zero-expectancy result predicts.
3. **The challenge is a squeeze between two failure modes.** Too little risk and
   accounts die of the *deadline* (125 of 200 expired at 0.5%); too much and they
   die of *drawdown* (194 of 200 breached at 3%).

**26.5% against a $500 fee is roughly $1,900 of fees per Phase 1 pass**, before
Phase 2 compounds the same odds. Whether the campaign clears its costs is still open
— see [`REMAINING.md`](REMAINING.md) §2.3.

### Boom/Crash spikes look memoryless

Preliminary, from 24h of ticks. Inter-spike coefficient of variation measures
**0.987** (Boom) and **1.072** (Crash) against **1.000** for a memoryless Poisson
process, and the empirical hazard is flat with age. Drift over one mean interval
almost exactly cancels the mean spike, so the cycle nets ~zero by design.

Underpowered at ~90 spikes — needs ~12 days of recording to confirm.

---

## Quickstart

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt      # pinned; reproducible

./.venv/bin/python -m pytest                      # 44 tests, no network needed

export PYTHONPATH=.
./.venv/bin/python scripts/verify_symbols.py    # verify codes against the live API
./.venv/bin/python scripts/fetch_history.py     # populate the raw store
./.venv/bin/python scripts/measure_spread.py    # spread -> timeframe decision
./.venv/bin/python scripts/test_leakage.py      # no-lookahead gate (must pass)
./.venv/bin/python scripts/validate_synth.py    # synthetic vs real ticks
./.venv/bin/python scripts/sweep_risk.py 200    # P(pass) surface (~90 min)
./.venv/bin/python scripts/run_campaign.py 200  # expected 12-mo net after fees
```

Run the tick recorder continuously — **Deriv serves only 24h of tick history, so
anything not captured live is lost permanently**:

```bash
PYTHONPATH=. setsid nohup ./.venv/bin/python scripts/record_ticks.py \
    > logs_recorder.txt 2>&1 < /dev/null &
```

> Use bracket patterns when checking on it — `pgrep -f '[r]ecord_ticks.py'`. A bare
> `-f record_ticks.py` also matches the shell running the command.

---

## Layout

```
propfirm/
  data/       Deriv WS client, symbol verification, history paging, parquet store,
              MT5 tick reader, forward recorder, tick synthesis
  sim/        contract spec, no-lookahead feed, fill engine, ledger, engine loop
  rules/      prop-firm rulesets, breach detection, career payout ledger, campaign
  strategy/   control strategies (random entry, no-trade)
  research/   Monte Carlo harness, Boom/Crash spike analysis
  llm/        (empty -- Phase 3)
scripts/      operational entry points
tests/        pytest suite (leakage, determinism, ledger, fills, breach, career)
data/raw/     immutable store (gitignored; ticks are NOT regenerable)
docs/         measurements and findings
```

### Design commitments worth knowing

**No lookahead, structurally.** The feed owns the data and hands out read-only
views sliced to the cursor, so the future is *absent* rather than merely
off-limits. `scripts/test_leakage.py` poisons the future with a sentinel and
asserts it never surfaces.

**Tick-level fills, never bar-level.** At 75% volatility, whether the stop or the
target was touched first inside a bar decides much of the result.

**Honest gaps.** A stop is a trigger, not a guaranteed price: jumping past the level
fills at the price actually available. Modelling stops as exact fills is the largest
source of fake backtest profit, and is catastrophic on Boom/Crash where spikes gap
through stops by design.

**Provenance as data.** Contract fields carry a `Provenance` enum, not a comment, so
an unverified number cannot quietly reach a headline result. Eight sizing fields are
currently `UNVERIFIED` — see [`REMAINING.md`](REMAINING.md) §3.1.

**Rules are data.** A different firm is a different config, not a different program.
The strict default uses equity-trailing drawdown, a 40% consistency cap, and a
four-day minimum. The last two are load-bearing: with zero edge the mathematically
optimal play is one huge bet, and those rules are what forbid it.

---

## Status

| Phase | | |
|---|---|---|
| 0 | Data layer, spread measurement, timeframe decision | done |
| 1 | Tick-level engine, leakage gate | done |
| 2 | Rule engine, Monte Carlo, controls | done |
| 3 | Opus decision layer, A/B against the core | not started |
| 4 | Research loop, pre-registration, champion/challenger | not started |
| 5 | Boom/Crash hazard study at full power | recording |

Known bugs, unverified numbers, and open questions:
[`REMAINING.md`](REMAINING.md).
