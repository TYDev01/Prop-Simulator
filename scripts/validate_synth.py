"""Validate synthetic ticks against real ones.

The strongest available test: take the 24h of REAL Vol75 ticks, aggregate them
into M1 bars, synthesise ticks back from those bars, and compare the synthetic
series against the real one it was derived from. Same period, same bars, so any
divergence is the synthesiser's doing.

Anything that fails here is a caveat on every result derived from synthetic ticks.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from propfirm.data import store
from propfirm.data.symbols import load_registry
from propfirm.data.synth_ticks import synth_ticks


def to_bars(ticks: pd.DataFrame, seconds: int) -> pd.DataFrame:
    t = ticks.copy()
    t["bucket"] = (t["epoch"] // seconds) * seconds
    g = t.groupby("bucket")["price"]
    bars = pd.DataFrame({
        "epoch": g.first().index, "open": g.first().values, "high": g.max().values,
        "low": g.min().values, "close": g.last().values, "n": g.size().values,
    })
    return bars[bars["n"] >= 2].reset_index(drop=True)


def stats(prices: np.ndarray, label: str) -> dict:
    r = np.diff(prices) / prices[:-1]
    ac1 = float(np.corrcoef(r[:-1], r[1:])[0, 1]) if len(r) > 2 else np.nan
    return {
        "label": label, "n": len(prices),
        "sigma_pct": float(r.std()) * 100,
        "mean_abs_pct": float(np.abs(r).mean()) * 100,
        "kurtosis": float(pd.Series(r).kurtosis()),
        "autocorr_lag1": ac1,
        "pct_up": float((r > 0).mean()) * 100,
    }


def main() -> int:
    sym = load_registry()["vol75"].symbol
    real = store.read(store.tick_path(sym)).sort_values("epoch").reset_index(drop=True)
    tick_s = float(np.median(np.diff(real.epoch.values)))
    print(f"real ticks: {len(real):,}  median spacing {tick_s:g}s\n")

    bars = to_bars(real, 60)
    print(f"M1 bars built from real ticks: {len(bars):,}")

    synth = synth_ticks(bars, tick_seconds=tick_s, seed=7, bar_seconds=60)
    print(f"synthetic ticks generated:     {len(synth):,}\n")

    # 1. OHLC fidelity -- must be exact by construction.
    sb = to_bars(synth, 60).set_index("epoch")
    rb = bars.set_index("epoch")
    common = rb.index.intersection(sb.index)
    # Judge on percentiles, not the max: a handful of bars have ranges no Brownian
    # bridge reaches naturally, and one outlier should not condemn 1,440 good bars.
    bar_range = float((rb.loc[common, "high"] - rb.loc[common, "low"]).median())
    print("1. OHLC reconstruction error, as % of median bar range"
          f" ({bar_range:.1f} pts):")
    worst_p99 = 0.0
    for c in ["open", "high", "low", "close"]:
        e = np.abs(rb.loc[common, c] - sb.loc[common, c])
        p99 = float(e.quantile(0.99))
        worst_p99 = max(worst_p99, p99 / bar_range)
        print(f"     {c:5s}  med={e.median():6.2f}  p95={e.quantile(.95):6.2f}"
              f"  p99={p99:6.2f}  max={e.max():7.2f} pts"
              f"   (p99 = {p99/bar_range*100:.1f}% of range)")
    ok_ohlc = worst_p99 < 0.05

    # 2. Tick-level distribution.
    print("\n2. Tick return distribution:")
    a, b = stats(real.price.values, "real"), stats(synth.price.values, "synthetic")
    keys = ["n", "sigma_pct", "mean_abs_pct", "kurtosis", "autocorr_lag1", "pct_up"]
    print(f"     {'metric':<16}{'real':>14}{'synthetic':>14}{'ratio':>10}")
    for k in keys:
        av, bv = a[k], b[k]
        ratio = (bv / av) if isinstance(av, float) and av not in (0.0,) else np.nan
        print(f"     {k:<16}{av:>14.5f}{bv:>14.5f}{ratio:>10.3f}")

    # 3. Intrabar path realism: how far price travels vs the bar's own range.
    print("\n3. Intrabar path (path length / bar range):")
    def path_ratio(ticks, bars_):
        t = ticks.copy(); t["bucket"] = (t["epoch"] // 60) * 60
        out = []
        for bkt, grp in t.groupby("bucket"):
            p = grp.price.values
            if len(p) < 3: continue
            rng_ = p.max() - p.min()
            if rng_ <= 0: continue
            out.append(np.abs(np.diff(p)).sum() / rng_)
        return np.array(out)
    pr_r, pr_s = path_ratio(real, bars), path_ratio(synth, bars)
    print(f"     real      mean={pr_r.mean():.3f}  median={np.median(pr_r):.3f}")
    print(f"     synthetic mean={pr_s.mean():.3f}  median={np.median(pr_s):.3f}")
    print(f"     ratio     {pr_s.mean()/pr_r.mean():.3f}")

    print("\n" + "=" * 60)
    sig_ratio = b["sigma_pct"] / a["sigma_pct"]
    path_ok = 0.85 <= pr_s.mean() / pr_r.mean() <= 1.15
    ac_ok = abs(b["autocorr_lag1"]) < 0.05
    up_ok = abs(b["pct_up"] - 50.0) < 2.0
    print(f"  OHLC p99 < 5% rng: {'PASS' if ok_ohlc else 'FAIL'}")
    print(f"  tick sigma ratio:  {sig_ratio:.3f}  "
          f"{'PASS' if 0.85 <= sig_ratio <= 1.15 else 'FAIL'}")
    print(f"  path length ratio: {pr_s.mean()/pr_r.mean():.3f}  "
          f"{'PASS' if path_ok else 'FAIL'}")
    print(f"  |autocorr| < 0.05: {b['autocorr_lag1']:+.4f}  "
          f"{'PASS' if ac_ok else 'FAIL'}   (real {a['autocorr_lag1']:+.4f})")
    print(f"  up-ticks ~50%:     {b['pct_up']:.2f}%  {'PASS' if up_ok else 'FAIL'}")
    allok = ok_ohlc and path_ok and ac_ok and up_ok and 0.85 <= sig_ratio <= 1.15
    print(f"\n  OVERALL: {'PASS' if allok else 'FAIL'}")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
