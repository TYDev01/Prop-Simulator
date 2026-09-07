"""OHLC-conditioned tick synthesis for Volatility 75.

Why this is legitimate here. Deriv serves only 24h of tick history, but the spec
mandates tick-level simulation (section 5) and we want a year of replayable data.
For a real market, inventing intra-bar ticks would be indefensible -- you would be
fabricating the microstructure you are trying to measure. For Vol75 it is
different: the generating process is *known and published* (geometric Brownian
motion at constant 75% annualised volatility, no drift). Synthesising a path that
is Brownian between known OHLC anchors reproduces the true process rather than
guessing at one.

The synthetic path is nonetheless VALIDATED against the 24h of real ticks we hold
(scripts/validate_synth.py). Anything it fails to reproduce is a caveat on every
result derived from it.

Two functions, for two genuinely different needs:

  gbm_ticks()   Unconditioned GBM at the true 75% annualised vol. Exactly the real
                process, conditioned on nothing. This is what Monte Carlo, the
                control arms, and P(pass) estimation should use -- none of them
                need to reproduce any particular historical bar.

  synth_ticks() Bridge-conditioned on real OHLC, for replaying actual history where
                intra-bar order of stop/target hits matters.

Method for the conditioned case: exact OHLC *and* exact Brownian character is an
over-constrained problem, and forcing both deforms the path. So we choose by
SELECTION rather than deformation -- draw many candidate bridges at the true sigma,
keep the one whose natural extremes come closest to (H, L), then snap only those two
points. With enough candidates the snap is small and local.

Two earlier attempts were rejected by validation, and are recorded here because both
looked fine on OHLC alone:

  1. Walking O -> extreme -> extreme -> C through forced anchors gave lag-1
     autocorrelation of 0.13 against a real 0.002, and 43% up-ticks against a real
     50%. Manufactured momentum: a strategy backtested on it would find a trend edge
     that does not exist.
  2. Rescaling deviations to span [L, H] then clipping gave autocorrelation -0.17,
     kurtosis 11.8 against a real -0.03, and 1.6x the true tick sigma.

OHLC fidelity alone does not make a synthetic path faithful.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from propfirm.config import SECONDS_PER_YEAR, VOL75_ANNUAL_VOL

def per_tick_sigma(tick_seconds: float, annual_vol: float = VOL75_ANNUAL_VOL) -> float:
    """Log-return sigma of a single tick, as a fraction of price."""
    return annual_vol * (tick_seconds / SECONDS_PER_YEAR) ** 0.5


def _bridge(a: float, b: float, m: int, sigma_abs: float,
            rng: np.random.Generator) -> np.ndarray:
    """Brownian bridge of m points from a (exclusive) to b (inclusive)."""
    if m <= 0:
        return np.empty(0)
    if m == 1:
        return np.array([b])
    steps = rng.normal(0.0, sigma_abs, m)
    w = np.cumsum(steps)
    t = np.arange(1, m + 1) / m
    w = w - w[-1] * t                    # pin the bridge to zero at the far end
    return a + (b - a) * t + w


def gbm_ticks(n_ticks: int, start_price: float, tick_seconds: float,
              rng: np.random.Generator,
              annual_vol: float = VOL75_ANNUAL_VOL) -> np.ndarray:
    """Unconditioned GBM path -- the true Vol75 process, conditioned on nothing.

    Use this for Monte Carlo, control arms, and P(pass) estimation. It needs no
    validation against history because it *is* the published generating process.
    """
    sig = per_tick_sigma(tick_seconds, annual_vol)
    incr = rng.normal(-0.5 * sig ** 2, sig, n_ticks)
    return start_price * np.exp(np.cumsum(incr))


def _bridge_path(o: float, c: float, n: int, sigma_abs: float,
                 rng: np.random.Generator) -> np.ndarray:
    """Brownian bridge pinned at o and c, at the true tick sigma."""
    t = np.arange(n) / (n - 1)
    line = o + (c - o) * t
    w = np.cumsum(rng.normal(0.0, sigma_abs, n))
    w = w - w[0]
    return line + (w - w[-1] * t)


def synth_bar(o: float, h: float, l: float, c: float, n_ticks: int,
              sigma_abs: float, rng: np.random.Generator,
              n_candidates: int = 128) -> np.ndarray:
    """Generate n_ticks prices for one bar, approximating O/H/L/C.

    Chooses among candidate bridges rather than deforming one, then snaps the two
    extreme points. Residual OHLC error is reported by scripts/validate_synth.py
    rather than forced to zero -- forcing it is what wrecked earlier versions.
    """
    if n_ticks <= 1:
        return np.array([c])
    if h <= l:
        return np.full(n_ticks, c)

    best, best_err = None, np.inf
    for _ in range(n_candidates):
        path = _bridge_path(o, c, n_ticks, sigma_abs, rng)
        err = abs(path.max() - h) + abs(path.min() - l)
        if err < best_err:
            best, best_err = path, err

    path = best.copy()
    path[int(np.argmax(path))] = h
    path[int(np.argmin(path))] = l
    path[0], path[-1] = o, c
    return np.clip(path, l, h)


def synth_ticks(bars: pd.DataFrame, tick_seconds: float, seed: int = 0,
                bar_seconds: int = 60, n_candidates: int = 128) -> pd.DataFrame:
    """Expand a DataFrame of OHLC bars into a synthetic tick series.

    `bars` needs columns [epoch, open, high, low, close]. Returns [epoch, price].
    """
    rng = np.random.default_rng(seed)
    n_per_bar = max(1, int(round(bar_seconds / tick_seconds)))
    sig_frac = per_tick_sigma(tick_seconds)

    bars = bars.sort_values("epoch").reset_index(drop=True)
    epochs, prices = [], []
    for row in bars.itertuples(index=False):
        path = synth_bar(float(row.open), float(row.high), float(row.low),
                         float(row.close), n_per_bar,
                         sigma_abs=sig_frac * float(row.close), rng=rng,
                         n_candidates=n_candidates)
        base = int(row.epoch)
        epochs.append(base + (np.arange(len(path)) * tick_seconds).astype(int))
        prices.append(path)

    df = pd.DataFrame({"epoch": np.concatenate(epochs),
                       "price": np.concatenate(prices)})
    df = df.drop_duplicates(subset=["epoch"]).sort_values("epoch").reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["epoch"], unit="s", utc=True)
    return df
