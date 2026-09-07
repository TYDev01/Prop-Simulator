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

### 2.2 [GAP] The research loop — `propfirm/research/` has only Monte Carlo
Required per spec section 8:
- Per-trade logging (features at entry, decision, reasoning, declared
  invalidation, outcome, MAE/MFE). MAE/MFE are computed but not persisted.
- Daily adherence audit, kept separate from strategy change
- Pre-registration: predicted effect and falsification criterion recorded *before*
  testing
- Validation gate: discovery/validation/holdout partitioning, holdout touched once
- **Running multiple-testing ledger that never resets**
- Champion/challenger with out-of-sample promotion margin
- Strategy versioning with rationale, evidence, and test result per change
- Calibration score for Opus-as-researcher (how often do its predictions hold?)

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

### 2.5 [GAP] Order types
Spec section 5 requires market, limit, stop, trailing stop, break-even shift, and
partial closes. Only market orders with static SL/TP exist.

### 2.6 [GAP] Controls are not run alongside
Spec section 9.1 requires random-entry and synthetic-GBM controls running
*continuously*, not as one-offs. `gbm_ticks()` exists but no comparison harness runs
a candidate against both arms automatically.

### 2.7 [GAP] Long-history replay
M1 covers only ~6 weeks. Synthesis from M15 for the full 365 days is designed but
not implemented, and its lower intra-bar resolution needs its own validation.

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
