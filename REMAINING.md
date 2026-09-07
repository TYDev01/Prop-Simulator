# Remaining work

Status as of 2026-09-07. Phases 0-2 of `BUILD_PROMPT.md` are complete; Phases 3-5
are not started. Items are grouped by kind and ordered by priority within each
group.

Legend: **[BUG]** wrong behaviour · **[GAP]** missing capability · **[VERIFY]**
number we are relying on but have not confirmed · **[RISK]** operational hazard

---

## 1. Known bugs

### 1.1 [FIXED] Fetch errors are no longer misreported as "history exhausted"
`propfirm/data/history.py`. `DerivClient` now raises a distinct `RateLimit`
subclass; `history._send_with_backoff` retries `RateLimit` with exponential backoff
and lets every other `DerivError` propagate, so `exhausted` is reserved for a
genuinely empty (or short) response. Covered by `tests/test_history.py`.

**Still open:** the measured depth limits (24h ticks, 365d candles, ~60k M1 bars)
were established under the old swallowing code path. They are probably real — request
counts line up with clean multiples — but should be **re-confirmed** now that the
error handling distinguishes causes.

### 1.2 [FIXED] Margin stop-out is now enforced
The engine calls `SimEngine._enforce_stop_out` each tick after stops resolve: while
`Ledger.margin_level()` is below `ContractSpec.stop_out_level_pct` it force-closes
the worst-loss position, mirroring the broker's own liquidation. Still usually
dormant (prop rules breach first) but active for high-leverage configs. Covered by
`tests/test_engine.py`. Note `stop_out_level_pct` defaults to 50.0 and is flagged
`UNVERIFIED` (see 3.1).

### 1.3 [FIXED] Swap/rollover is now accrued
`Ledger._accrue_swap` charges `ContractSpec.swap_charge(direction, lots)` on every
open position at each broker-day rollover, tracked in `Ledger.swap_paid`. The
mechanism is complete and tested (`tests/test_ledger.py`), but with both swap rates
still `0.0`/`UNVERIFIED` it currently charges nothing — real values are needed
(see 3.1), as is triple-Wednesday accrual if it matters.

---

## 2. Missing capability

### 2.1 [DONE] The LLM layer — `propfirm/llm/`
Phase 3 scaffolding is built, behind a provider interface so the whole overlay is
exercisable offline at zero cost:
- **State-packet builder** (`state_packet.py`) — recent bars, candidate features,
  position state, and account state including distance to both loss floors, days
  elapsed, and progress to target.
- **Strict JSON schema + validation** (`schema.py`) — `DECISION_JSON_SCHEMA` for
  `output_config.format`, and `parse_decision`, which degrades any malformed
  response to a logged no-trade (never raises).
- **Opus client with cost accounting + full call logging** (`provider.py`) —
  `OpusProvider` (lazy `anthropic` import, `CostMeter`, JSONL prompt/response log)
  and `MockProvider` (free, deterministic; used by the tests and the offline A/B).
- **Dual-mode switch** (`overlay.py`) — the seed exposes candidates via a `decide`
  hook; `dual_mode()` returns core-only and core+overlay factories from one config
  so both run on identical data. Hard risk limits stay in Python: the overlay may
  only tighten risk, never raise it. A/B entry point: `scripts/run_overlay_ab.py`.
  Tests in `tests/test_llm.py`.

**Not yet run live:** a real Opus A/B spends money and needs credentials, so the
script wires `OpusProvider` but leaves firing it — and the LLM budget decision
(§7.4) — to the user. Per-trade research logging (§2.2) is still the next gap.

### 2.2 [DONE] The research loop — `propfirm/research/`
Built per spec section 8/9.1, all pure-Python and tested (`tests/test_research.py`),
tied together end-to-end by `scripts/research_demo.py`:
- **Per-trade logging** (`trade_log.py`) — features at entry, declared invalidation,
  overlay reasoning, outcome, MAE/MFE, and realised R. Entry metadata now flows
  candidate → position → closed trade via a `meta` dict.
- **Pre-registration + calibration** (`preregistration.py`) — predicted effect,
  falsification criterion, and price level recorded *before* the test; a hit-rate
  and Brier calibration score for the researcher.
- **Validation gate** (`partition.py`) — discovery/validation/holdout seed split
  with a `HoldoutGuard` that raises on a second access (touch-once, enforced).
- **Running multiple-testing ledger that never resets** (`multiple_testing.py`) —
  Bonferroni bar that tightens with every test, Benjamini-Hochberg FDR, JSONL
  persistence that restores the count across restarts.
- **Champion/challenger** (`champion_challenger.py`) — out-of-sample promotion
  margin, versioning with rationale/evidence/test-result per change.
- **Partition-aware evaluation** (`evaluate.py`) — scores a factory over a specific
  seed set; `compare` runs two arms on identical seeds with a two-proportion test.
- **Daily rule-adherence audit** (`adherence.py`) — the second cadence, kept separate
  from strategy change: per-day loss-room used, stop-slippage past 1R, the
  consistency cap, and min-trading-days progress, in realised terms.

**Still open:** driving these from a live *Opus* researcher (auto hypothesis
generation) needs the LLM budget (§7.4). The demo uses a fixed hypothesis.

### 2.3 [DONE] Career progression and payout are wired
`propfirm/rules/campaign.py` drives the full career: fee → phase 1 → phase 2 →
funded → 14-day payout cycles → breach → buy another, over a 365-day horizon that
each phase/cycle draws down as a time budget (via `RunResult.elapsed_days`).
`run_campaign` runs many careers in parallel and `summarise_campaign` reports the
headline **expected 12-month net after fees** as a distribution. Entry point:
`scripts/run_campaign.py`; tests in `tests/test_campaign.py`.

**Caveats still open:** the funded stage models each 14-day cycle as a fresh
base-size account with profit withdrawn (drawdown resets to base); triple-Wednesday
swap and partial payouts are not modelled; the headline number inherits every
`UNVERIFIED` sizing field from 3.1, so its *magnitude* is not yet trustworthy even
though the machinery is correct.

### 2.4 [DONE] Seed strategy
`propfirm/strategy/seed_mae.py` implements the reduced M.A.E. skeleton from spec
section 7: swing-based **structural stops**, **multi-filter selectivity** (donchian
breakout + slow-bias agreement + cooldown, all as cost control), and **declared
invalidation** recorded on the position before entry. Signals use completed bars
only; entries execute at the current tick, so there is no lookahead. Deterministic
given the path. `ReducedMAEFactory` makes it drop-in for Monte Carlo and the
campaign; `scripts/compare_seed.py` runs it against random entry on identical seeds
(the §9.1 A/B). Tests in `tests/test_seed_mae.py`.

As §2.2 requires, this is expected to be indistinguishable from random entry — it is
the seed the research loop (Phase 4) evolves, not a claimed edge.

### 2.5 [DONE] Order types
Spec section 5's full set now exists (`propfirm/sim/orders.py` + engine/ledger):
- **Limit and stop entries** — resting `PendingOrder`s resolved each tick; a limit
  fills at its level, a stop at the worse of its level and the available price (gap
  honesty), with optional GTD expiry. `ctx.buy_limit/sell_limit/buy_stop/sell_stop`.
- **Trailing stop** — `trail_distance` ratchets the stop behind price and never
  loosens on a gap; resolved against the step's stop, then trailed for the next tick.
- **Break-even shift** — `breakeven_trigger` (+ optional offset) moves the stop to
  entry once price reaches the trigger.
- **Partial closes** — `ctx.close_partial(pos, fraction)` banks part of a position
  and keeps the rest; a fraction that would strand a sub-min-lot remainder closes
  fully instead.
Tests in `tests/test_orders.py` (11). A freshly-filled order is never resolved on its
own opening tick.

### 2.6 [DONE] Controls run alongside
`research/evaluate.py::compare` runs a candidate against the random-entry control on
identical seeds with a significance test, and `champion_challenger.consider` scores
both arms out-of-sample as a matter of course. The matched-vol GBM control arm is now
wired too: `run_trials` takes a pluggable `path_fn`, and `compare_sources` runs one
strategy across two tick sources (`gbm_path` vs `matched_gbm`), correctly reporting
no difference for the null. **The one piece still data-gated:** the real-Vol75-vs-GBM
comparison (§6) needs captured real ticks to plug in as the second source — the
harness is ready, the data is not.

### 2.7 [DONE] Long-history replay
Implemented and validated. `propfirm/data/replay.py` expands M15 (or any) OHLC bars
into a tick series with the M1 bridge synthesis, chunked to bound memory over a full
year (~35k bars → ~15.7M 2s ticks); `data/bars.py` aggregates ticks → OHLC;
`data/synth_validate.py` runs the increment diagnostics.

**Validated finding** (`scripts/validate_m15_replay.py`, `docs/m15_replay_validation.json`):
M15 replay reproduces per-tick sigma (~0.8% error), lag-1 autocorrelation (≈0, no
manufactured momentum) and up-tick balance (≈50%). It **fails** the kurtosis check
(~+2.2 vs a real ≈0) because snapping the two extremes to the exact H/L over a 900s
bar creates wick-spikes that fatten the increment tails — the exact resolution risk
§2.7 warned about. **But the decisive test passes:** P(pass) on the true tick path vs
its M15 replay is statistically indistinguishable (27.5% vs 22.5%, p≈0.61), so the
replay is fit for P(pass) estimation with the kurtosis caveat noted. A less spiky
snap (distributing the H/L adjustment over neighbours) would close the gap if any
future use needs faithful tails. Tests: `tests/test_replay.py`.

---

## 3. Numbers we rely on but have not verified

### 3.1 [VERIFY] Contract specification — blocks quantitative claims
Nine fields are flagged `UNVERIFIED` in `propfirm/sim/contract.py`:
`contract_size`, `min_lot`, `max_lot`, `lot_step`, `leverage`,
`stops_level_points`, `swap_long`, `swap_short`, `stop_out_level_pct`.

P&L magnitudes are structurally right but **not quantitatively trustworthy** until
these are confirmed. `contract_size` and `leverage` scale every result directly.

Two-minute fix: MT5 → right-click *Volatility 75 Index* → Specification.

### 3.2 [VERIFY] Is the 4.50-point spread stable?
Measured constant across 2048 ticks — but that is a 36-minute window from
2026-05-23, at an index price of 28,279. Price is now ~49,800. Unknown whether
spread is fixed in points forever, or periodically re-pegged to price level.

This matters more than it sounds: spread fixed in points means percentage cost moves
inversely with a price that is itself a random walk, so a viable configuration can
become unviable through no change in strategy.

### 3.3 [VERIFY] Boom/Crash spread
`BOOM1000` spread was never measured — the MT5 cache held Crash 1000 but not Boom.
Crash measured 0.058 points (0.0010% of price).

### 3.4 [VERIFY] Spike hazard at full statistical power
Preliminary CV: BOOM 0.987, CRASH 1.072, against 1.000 for memoryless. From ~90
spikes each, where the standard error on CV is roughly 0.07 — **neither is
distinguishable from 1.000.** Needs ~1000+ spikes, i.e. ~12 days of recording.

If it holds, tick-counting is worthless and Boom/Crash is zero-edge like Vol75. If
it does not, the roadmap reorders. Re-run `scripts/spike_study.py` as data lands.

### 3.5 [VERIFY] Resolution sensitivity used only 12 seeds
The finding that coarse sampling biases results pessimistically (4/12 pass at 2s vs
1-2/12 coarser) is directionally consistent across three step sizes but individually
weak. Re-run at 200 seeds to confirm the effect size.

### 3.6 [VERIFY] Synthetic tick kurtosis
Synthesiser passes all five validation checks but shows kurtosis +0.10 against a
real -0.03 — very slightly fat-tailed. Re-check against a longer real-tick window
once the recorder has accumulated one.

---

## 4. Operational risks

### 4.1 [RISK] Recorded ticks are irreplaceable and have no backup
Deriv serves only 24h of tick history. Everything the recorder captures beyond that
window **exists nowhere else**. It is gitignored (correctly — it grows without
bound), which means it currently has no durable copy at all.

Needs an rsync target or object storage before the ~12 days of hazard-study data
accumulate.

### 4.2 [RISK] The recorder has no supervision
It died once already (SIGTERM when its parent shell exited; now launched under
`setsid`). There is no health check, no auto-restart, and no alert. A silent death
costs data permanently. Needs a systemd unit or a watchdog.

### 4.3 [RISK] Stream chunks are never compacted automatically
`recorder.compact()` must be called by hand. Chunks accumulate indefinitely (7
pending at time of writing). Needs scheduling.

### 4.4 [RISK] `pkill -f` / `pgrep -f` self-match
A pattern matching the command string also matches the shell running it. This
killed the session twice, once silently dropping a code edit that was assumed
applied. Always use bracket patterns: `pgrep -f '[r]ecord_ticks.py'`.

---

## 5. Testing

### 5.1 [DONE] Test suite
`tests/` now holds a pytest suite (44 tests, `python3 -m pytest`) covering the items
below. The ad-hoc scripts remain as operational entry points.
- Leakage gate as a test (`test_leakage.py`)
- Determinism — identical inputs ⇒ bit-identical outputs (`test_engine.py`)
- Ledger arithmetic against hand-worked examples (`test_ledger.py`)
- Gap-fill behaviour: stop *through* a level fills worse; target does not pay better
  (`test_fills.py`)
- Breach detection at exact boundaries (`test_breach.py`)
- Day-rollover at the 00:00 GMT+2 boundary, including the integer fast path
  (`test_ledger.py`)
- Margin stop-out and swap accrual (`test_engine.py`, `test_ledger.py`)
- Rate-limit retry vs genuine exhaustion (`test_history.py`)
- `CareerLedger` end-to-end (`test_career.py`)

### 5.2 [GAP] No CI
Suite runs locally; not yet wired to a CI trigger.

### 5.3 [DONE] Dependency pinning
`requirements.txt` pins the versions Phase 0–2 ran under. The live-data dependency
(`websockets`) is now imported lazily, so the simulator, tests, and offline analysis
run without it installed.

---

## 6. Research not yet run

- **Component ablation** on the seed strategy: does each filter earn its place?
- **Synthetic-GBM control comparison**: strategy on real Vol75 vs matched-vol GBM.
  Identical results ⇒ the "structure" being traded does not exist.
- **Boom/Crash three-way**: M.A.E.-as-written vs the inversion vs random entry, with
  honest spike gap-fills. Note the M.A.E. inversion hazard in spec section 12 —
  applied faithfully the method goes long Crash and short Boom, standing in front of
  the spike with maximum negative skew.
- **RNG artefact hunt** on Vol75: autocorrelation, runs tests, tick-level
  periodicity. Expect nothing; it is cheap and it is the only place an edge could
  hide.
- **Price-regime validation**: because spread cost depends on price level, every
  adopted rule must be checked across regimes. The multiple-testing ledger should
  record the price level at adoption.
- **Phase 2 and funded-stage pass rates**, then the full campaign economics.

---

## 7. Open questions for the user

1. **Contract spec values** (3.1) — the one blocker on trustworthy P&L magnitudes.
2. **Is 26.5% enough to continue?** Best measured configuration is 1% risk, 1:2 RR,
   3 trades/day. Expected fee cost per Phase 1 pass is ~$1,900 at a $500 fee, before
   Phase 2 compounds the same odds. The campaign economics may not clear the fee,
   and that is a legitimate reason to stop.
3. **Backup destination** for irreplaceable tick data (4.1).
4. **LLM budget** — cost ceiling for the Phase 3 overlay and the research loop.
