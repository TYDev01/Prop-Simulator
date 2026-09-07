# Remaining work

Status as of 2026-09-07. Phases 0-2 of `BUILD_PROMPT.md` are complete; Phases 3-5
are not started. Items are grouped by kind and ordered by priority within each
group.

Legend: **[BUG]** wrong behaviour · **[GAP]** missing capability · **[VERIFY]**
number we are relying on but have not confirmed · **[RISK]** operational hazard

---

## 1. Known bugs

### 1.1 [BUG] Fetch errors are silently reported as "history exhausted"
`propfirm/data/history.py` — both `fetch_candles` and `fetch_ticks`:

```python
except DerivError:
    exhausted = True
    break
```

**Any** API error — rate limit, transient failure, bad parameter — ends the paging
loop and returns partial data flagged `exhausted=True`, i.e. indistinguishable from
"the API has no more history." A rate limit hit mid-fetch therefore looks like a
genuine depth limit.

This matters because the measured depth limits (24h ticks, 365d candles, ~60k M1
bars) were established with exactly this code path. They are probably real — the
request counts line up with clean multiples — but **they should be re-confirmed
once the error handling distinguishes causes.**

Fix: catch `RateLimit` separately and retry with backoff; re-raise unexpected codes;
reserve `exhausted` for a genuinely empty response.

### 1.2 [BUG] Margin stop-out is never enforced
`Ledger.margin_level()` exists and is never called. A position can run the account
to arbitrarily negative equity without the broker closing it. Currently masked
because prop rules breach long before stop-out, but it will matter for high-leverage
configurations.

### 1.3 [BUG] Swap/rollover is declared but never accrued
`ContractSpec.swap_long/swap_short` exist, default to `0.0`, and nothing charges
them. Multi-day holds are therefore free. Needs both the real values (see 3.1) and
accrual at the rollover boundary.

---

## 2. Missing capability

### 2.1 [GAP] The whole LLM layer — `propfirm/llm/` is empty
Nothing in Phase 3 exists. Required per spec section 3:
- State-packet builder (bars across three timeframes, features, position state,
  and account state including distance to both loss limits)
- Strict JSON response schema with validation; malformed ⇒ no trade, logged
- Opus client with cost accounting
- **Dual-mode switch** so deterministic-core and core+overlay run on identical
  data. Without this the central question — does the overlay beat the core? — is
  unanswerable.

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

### 2.3 [GAP] Career progression and payout are not wired
`CareerLedger` is implemented and tested in isolation but **nothing calls it**. The
engine runs a single phase. Missing: phase 1 → phase 2 → funded progression, payout
cycles, breach → buy another challenge, and the headline
**expected-12-month-net-after-fees** number. This is the metric the whole project
exists to produce.

### 2.4 [GAP] No seed strategy
Only controls exist (`RandomEntry`, `NoTrade`). The reduced M.A.E. skeleton from
spec section 7 — structural stops, multi-filter selectivity, declared invalidation —
is unbuilt, so there is nothing yet for the research loop to evolve.

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
Eight fields are flagged `UNVERIFIED` in `propfirm/sim/contract.py`:
`contract_size`, `min_lot`, `max_lot`, `lot_step`, `leverage`,
`stops_level_points`, `swap_long`, `swap_short`.

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

### 5.1 [GAP] No test suite
Verification lives in ad-hoc scripts (`test_leakage.py`, `validate_synth.py`) that
must be run manually. Needs pytest, so regressions surface automatically:
- Leakage gate as a test, not a script
- Determinism (identical inputs ⇒ bit-identical outputs)
- Ledger arithmetic against hand-worked examples
- Gap-fill behaviour: stop *through* a level fills worse; target does not pay better
- Breach detection at exact boundaries (equity precisely at the floor)
- Day-rollover at the 00:00 GMT+2 boundary, including the integer-arithmetic fast
  path introduced during optimisation
- `CareerLedger` end-to-end

### 5.2 [GAP] No CI
### 5.3 [GAP] No dependency pinning
Packages were installed ad-hoc into `.venv`. No `requirements.txt` or
`pyproject.toml`, so the environment is not reproducible.

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
