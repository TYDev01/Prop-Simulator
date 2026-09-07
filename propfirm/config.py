"""Central configuration. Anything a broker or firm decides is data, not code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
DATA_DERIVED = ROOT / "data" / "derived"
DOCS = ROOT / "docs"

# Deriv WebSocket API. Market data needs only an app_id; 1089 is Deriv's public
# test id. Override with DERIV_APP_ID for anything sustained.
DERIV_WS_URL = "wss://ws.derivws.com/websockets/v3"
DERIV_APP_ID = os.environ.get("DERIV_APP_ID", "1089")

# Deriv caps a ticks_history response at 5000 rows regardless of `count`.
MAX_ROWS_PER_REQUEST = 5000

# Conservative client-side pacing. Deriv rate-limits unauthenticated connections
# and responds with RateLimit errors rather than backpressure, so we self-throttle.
REQUESTS_PER_MINUTE = int(os.environ.get("DERIV_RPM", "45"))
REQUEST_TIMEOUT_S = 30.0

# Symbols we care about. These are HYPOTHESES to be checked against the live
# active_symbols list -- never trusted blind. See spec section 4.
CANDIDATE_SYMBOLS = {
    "vol75": ["R_75"],
    "vol75_1s": ["1HZ75V"],
    "boom1000": ["BOOM1000", "BOOM1000N"],
    "crash1000": ["CRASH1000", "CRASH1000N"],
}

# Granularities the legacy API accepted. Verified live by scripts/verify_symbols.py.
CANDIDATE_GRANULARITIES = [60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400]

# Volatility 75 is a constant 75% annualised-volatility process. Per-bar sigma
# follows from that directly and anchors every stop-distance calculation.
VOL75_ANNUAL_VOL = 0.75
SECONDS_PER_YEAR = 365 * 24 * 3600


def per_bar_sigma(granularity_s: int, annual_vol: float = VOL75_ANNUAL_VOL) -> float:
    """Expected log-return standard deviation of one bar, as a fraction of price."""
    return annual_vol * (granularity_s / SECONDS_PER_YEAR) ** 0.5


@dataclass(frozen=True)
class Timeframes:
    """Fixed by the Phase 0 spread measurement -- see docs/phase0_findings.md.

    Measured Vol75 spread is 4.50 index points, constant. Over a 200-trade
    challenge with a 1.5-sigma stop and 1% risk, spread costs this share of the
    8% Phase 1 target: M5 65-115%, M15 38-66%, H1 19-33%, H4 9-17%.

    M5 is disqualified outright. H1 is chosen over M15 because cost is the only
    lever that reliably works on a zero-edge instrument, and statistical power
    comes from parallel accounts rather than from overtrading one.
    """
    entry_s: int = 3600       # H1
    structure_s: int = 14400  # H4
    bias_s: int = 86400       # D1


TIMEFRAMES = Timeframes()


# Measured broker spread, Deriv-Demo MT5 tick cache, 2026-05-23 (2048 ticks).
# Fixed in POINTS, so its percentage cost moves inversely with the index price --
# and that price is itself a random walk. See docs/phase0_findings.md section 4.
MEASURED_SPREAD_POINTS = {
    "R_75": 4.50,
    "CRASH1000": 0.058,
}
