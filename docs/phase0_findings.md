# Phase 0 findings — data layer

Measured 2026-09-05. All numbers from live API or the broker's own tick cache.

## 1. Symbols verified live

| Alias | Code | Tick interval | Price at check |
|---|---|---|---|
| vol75 | `R_75` | **2s** | 49,766 |
| vol75_1s | `1HZ75V` | 1s | 6,488 |
| boom1000 | `BOOM1000` | **1s** | 14,787 |
| crash1000 | `CRASH1000` | **1s** | 6,072 |

All 12 legacy granularities (60s..86400s) are served for every symbol.

**Boom/Crash tick at 1s, not 2s.** So "1000 ticks between spikes" is roughly
**16.7 minutes**, not 33. This sets the sampling requirement for the hazard study:
~86 spikes/day, so a few weeks of ticks gives a few thousand inter-arrival
observations — comfortably enough to fit a distribution.

**Vol75 sits near 49,766, not the ~1,000,000 assumed from memory.** Vindicates the
rule against hardcoding instrument facts.

## 2. `active_symbols` is geo-restricted; data is not

From client country `ng`, `active_symbols` returns an **empty list** under every
parameter combination tried (brief/full, with and without product_type and
landing_company). That endpoint reports what is *tradable* in the caller's
jurisdiction.

`ticks_history` works normally for all four symbols. Since the simulator only ever
consumes data and never touches a real account, this does not block anything.
Symbol verification was therefore rebuilt to probe `ticks_history` directly, which
is a stricter test anyway: it proves the code returns the series we depend on.

## 3. Real broker spread (Deriv-Demo MT5 tick cache)

Deriv's WebSocket feed returns a single price per tick with **no bid/ask**, so it
cannot measure spread. Recovered instead from a cached MT5 terminal by reverse-
engineering `ticks.dat` (432-byte header, packed 60-byte `MqlTick` records).

| Symbol | Spread | Fraction of price | Variability |
|---|---|---|---|
| Volatility 75 Index | **4.50 pts** | 0.0158% @ 28,279 | **constant** across 2048 ticks |
| Volatility 25 Index | 0.234 pts | 0.0084% | constant |
| Crash 1000 Index | 0.058 pts | 0.0010% | 0.057–0.059 |

Sample is a ~36-minute ring buffer from 2026-05-23, so it is a solid spread probe
but not a long-horizon one. Worth re-checking whether 4.50 is fixed forever.

## 4. THE GATE: cost model and timeframe decision

Assumptions: stop = 1.5 sigma, risk = 1%/trade, 200 trades/challenge, P1 target 8%.

Cost of spread over one challenge, as a percentage of the 8% profit target:

| TF | sigma/bar | @ price 28,279 | @ price 49,787 |
|---|---|---|---|
| M5 | 0.231% | **115%** | 65% |
| M15 | 0.401% | 66% | 38% |
| H1 | 0.801% | **33%** | **19%** |
| H4 | 1.603% | 17% | 9% |
| D1 | 3.926% | 7% | 4% |

**M5 is disqualified.** At the May price level, spread alone costs *more than the
entire profit target*. The strategy would have to be brilliant just to break even.

**Finding worth flagging: the spread is fixed in POINTS, so its percentage cost
moves inversely with the index price — and that price is itself a driftless random
walk.** Vol75 went 28,279 -> 49,787 (1.76x), which cut cost per trade to 57% of what
it was. Your cost structure is therefore a random walk too. A configuration that is
viable at 50,000 can become unviable at 28,000 with no change in strategy. The sim
must model this, and timeframe selection should arguably be price-level aware.

### Decision: H1 entry / H4 structure / D1 bias

Revised down from the proposed M15/H1/H4. Reasoning:

- On a zero-edge instrument, **cost is the only lever that reliably works**
  (spec section 2.3). H1 halves M15's cost — 19% of target versus 38%.
- H1 gives ~720 bars in a 30-day challenge; with selectivity that is ~30-80 trades.
  Thin for a single account, but statistical power comes from running 100-200
  **parallel accounts** (spec section 8), not from overtrading one.
- Headroom matters: at an unlucky low price level H1 still costs only 33% of target,
  where M15 would cost 66%.

M15 stays available as a config flag for high-price regimes. The choice was made by
arithmetic, as the spec requires — not by preference.

---

# Phase 0 addendum — history limits, recorder, preliminary spike study

## 5. Deriv history depth is hard-capped

Measured by paging backwards until exhaustion:

| Data | Depth served | Note |
|---|---|---|
| Candles (any granularity) | **365 days** | 8,761 H1 bars, 34,999 M15 bars |
| **Ticks** | **exactly 24 hours** | 86,384 ticks at 1s; 43,190 at 2s |

**Tick history cannot be back-filled.** The spec mandates tick-level simulation
(section 5), so any tick not captured as it happens is permanently lost. This makes
the forward recorder the long pole of the entire project, and it is why it was
started before the sim engine.

Consequence for backtesting: tick-accurate replay over a year is impossible from
this source. Two viable paths, both worth building:
1. **Forward recording** — accumulates from now. Running.
2. **Calibrated tick synthesis** — Vol75's generating process is *known* (GBM at 75%
   annualised vol), so intra-bar ticks can be generated as a Brownian bridge
   consistent with real M1 OHLC, and validated against the 24h of real ticks we
   hold. Legitimate here precisely because the process is known, which is not true
   of a real market.

## 6. Streaming is geo-gated; the recorder polls

`{"ticks": SYM, "subscribe": 1}` and `ticks_history` with `subscribe: 1` both return
`InvalidSymbol` from client country `ng` — the same jurisdictional gate that empties
`active_symbols`. Plain `ticks_history` is unaffected.

The recorder therefore polls `ticks_history` every 20s requesting 200 ticks and
de-duplicates on epoch. Overlap is ~10x the poll interval, so gaps are impossible
unless the process is down. Cost: 9 requests/min against a 45/min budget.

## 7. Preliminary Boom/Crash hazard study

24h of ticks per symbol. Spike detection is unambiguous — drift ticks move ~0.0001%,
spikes ~0.3%, a separation of ~3 orders of magnitude, so any threshold in the gap
gives an identical spike set.

| | BOOM1000 | CRASH1000 |
|---|---|---|
| Spikes in 24h | 84 | 92 |
| Mean interval | **998.7 ticks** | 897.8 ticks |
| Median interval | 714 | 471 |
| Range | 1 .. 5,163 | 12 .. 5,240 |
| Drift over one interval | −0.1001% | +0.0901% |
| Mean spike (raw signed) | +0.1090% | −0.1147% |
| **Net per cycle** | **+0.0090%** | **−0.0246%** |
| **Coefficient of variation** | **0.987** | **1.072** |

### Two findings

**a) The cycle is engineered to net ~zero.** Drift accumulated over one mean interval
almost exactly cancels the mean spike, on both instruments and in opposite
directions. Boom/Crash are as close to zero-EV as Vol75, just with dramatically
different skew.

**b) The hazard looks FLAT — spikes appear memoryless.** CV is the diagnostic: an
exponential (Poisson) process gives exactly 1.000. BOOM measures 0.987, CRASH 1.072.
BOOM's empirical hazard by age is strikingly flat — 0.470, 0.455, 0.500, 0.500,
0.500 — showing no tendency for a spike to become more likely as one is "overdue".
BOOM also shows a minimum interval of **1 tick**: two spikes back to back, with no
refractory period, exactly as a memoryless process permits.

If this holds at full power, Deriv's public claim that "no amount of tick-counting
can tell you when the next one will arrive" is *literally true*, and the main
hypothesised edge in Boom/Crash does not exist.

### Power warning
84 and 92 spikes are **underpowered** for a confident distribution fit. A CV
estimated from ~90 samples has a standard error of roughly 0.07, so neither value is
distinguishable from 1.000. Confirming or refuting this needs ~1000+ spikes, i.e.
**~12 days of continuous recording**. The recorder is running; re-run
`scripts/spike_study.py` as data accumulates. Treat the current result as a strong
prior, not a conclusion.

---

# Phase 0 addendum 2 — tick synthesis, validated

Deriv serves 24h of ticks but 365 days of candles, so replayable tick history has
to be reconstructed. Two generators, for two genuinely different needs:

| Function | Conditioning | Use |
|---|---|---|
| `gbm_ticks()` | none | Monte Carlo, control arms, P(pass). Needs no validation: it *is* the published process. |
| `synth_ticks()` | real M1 OHLC | Replaying actual history, where intra-bar order of stop/target hits decides the result. |

## Validation method

The strongest test available: take the 24h of **real** ticks, aggregate to M1 bars,
synthesise ticks back from those bars, and compare against the real series they came
from. Same period, same bars, so any divergence is the synthesiser's fault.

## Two rejected implementations

Both matched OHLC perfectly and were still wrong. Recorded because the failure mode
is subtle and expensive:

| Attempt | sigma | autocorr | up-ticks | kurtosis |
|---|---|---|---|---|
| **Real ticks (truth)** | — | **+0.002** | **50.0%** | −0.03 |
| 1. Forced anchor walk O→ext→ext→C | 0.97x | **+0.132** | **43.3%** | +0.51 |
| 2. Rescale deviations, then clip | 1.61x | **−0.169** | 36.5% | +11.8 |
| 3. Candidate selection (adopted) | **1.003x** | **−0.011** | **49.8%** | +0.10 |

Attempt 1 injected lag-1 autocorrelation of 0.13 against a real 0.002 — **manufactured
momentum**. A strategy backtested on it would have discovered a trend edge that does
not exist in the instrument, and nothing about the OHLC match would have revealed it.
Attempt 2 fixed the momentum by over-correcting into mean reversion, and blew the tick
sigma out by 60% while producing kurtosis of 11.8.

**OHLC fidelity alone does not make a synthetic path faithful.** Any tick synthesiser
must be validated on the return distribution, not just on bar reconstruction.

## Adopted method

Exact OHLC *and* exact Brownian character is over-constrained, and forcing both
deforms the path. So choose by **selection, not deformation**: draw 128 candidate
bridges at the true tick sigma, keep the one whose natural extremes come closest to
(H, L), then snap only those two points.

Final validation, 1,440 M1 bars / 43,200 ticks:

| Check | Result | |
|---|---|---|
| OHLC p99 error < 5% of bar range | median and p95 error are **0** | PASS |
| tick sigma ratio | 1.003 | PASS |
| intrabar path length ratio | 0.997 | PASS |
| lag-1 autocorrelation | −0.011 (real +0.002) | PASS |
| up-tick share | 49.78% | PASS |

Residual: kurtosis +0.10 against a real −0.03, so the synthetic path is very slightly
fat-tailed. Small, but it should be re-checked once the recorder has accumulated
enough real ticks to compare over a longer window.
