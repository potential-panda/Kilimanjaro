"""Standardised statistics aggregation for scan results.

Output key names (used across all scan styles):
    n                number of trades with valid return
    avg_gross        mean signed return before costs (bps)
    avg_net          mean net return after costs (bps)
    hit_rate         fraction where gross return > 0
    hit_rate_net     fraction where gross return > costs_bps
    std_bps          std dev of gross returns
    sharpe_per_trade avg_net / std_net
    t_stat           avg_net / (std_net / sqrt(n))
    p_value_1sided   one-sided P(mean <= 0 | data) via t-distribution
    p_value_bootstrap one-sided p via circular moving-block bootstrap —
                     robust to autocorrelated / overlapping trade returns,
                     which make the i.i.d. t-test overconfident
    skewness         skewness of net returns
    total_net_bps    n * avg_net
    ic               Pearson corr(signal_strength, return_bps)  [optional]
    avg_hold_s       mean holding period in seconds             [optional]
    median_hold_s    median holding period                      [optional]
    median_gross     median gross return                        [optional]
    p25_bps          25th percentile gross return               [optional]
    p75_bps          75th percentile gross return               [optional]
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_BOOT_N = 500
_BOOT_SEED = 12345
_BOOT_MIN_N = 20
_BOOT_CHUNK = 100
# Above this, bootstrap on an order-preserving decimation: expression-style
# level signals can produce millions of overlapping trades per combo, and a
# full bootstrap there costs GBs of index math for a p-value that is pinned
# near 0/1 anyway. Decimation keeps the block structure and is conservative
# (smaller effective n) — fine for screening.
_BOOT_MAX_N = 100_000


def block_bootstrap_pvalue(
    net: np.ndarray,
    n_boot: int = _BOOT_N,
    seed: int = _BOOT_SEED,
) -> float:
    """One-sided p-value for H0: mean <= 0, via circular moving-block bootstrap.

    Trade returns are autocorrelated (overlapping horizons, regime clustering),
    so the i.i.d. t-test understates the variance of the mean. Blocks of length
    ~n^(1/3) preserve the local dependence structure under resampling. The
    series is centered (null: zero mean), resampled n_boot times, and the
    p-value is the fraction of bootstrap means >= the observed mean, with the
    +1 correction so p is never exactly 0.

    Deterministic for a given (series, n_boot, seed).
    """
    n = len(net)
    if n < _BOOT_MIN_N:
        return float("nan")
    if n > _BOOT_MAX_N:
        net = net[:: n // _BOOT_MAX_N + 1]
        n = len(net)

    obs = float(net.mean())
    centered = net - obs
    block = max(1, round(n ** (1.0 / 3.0)))
    n_blocks = int(np.ceil(n / block))
    rng = np.random.default_rng(seed)

    offsets = np.arange(block)
    n_ge = 0
    for chunk_start in range(0, n_boot, _BOOT_CHUNK):
        chunk = min(_BOOT_CHUNK, n_boot - chunk_start)
        starts = rng.integers(0, n, size=(chunk, n_blocks))
        idx = (starts[:, :, None] + offsets[None, None, :]).reshape(chunk, -1)[
            :, :n
        ] % n
        means = centered[idx].mean(axis=1)
        n_ge += int((means >= obs).sum())

    return float((1 + n_ge) / (n_boot + 1))


def compute_stats(
    returns: pd.Series,
    costs_bps: float,
    signal_strength: pd.Series | None = None,
    holding_seconds: pd.Series | None = None,
    episodes: pd.Series | None = None,
    overlap_ts: pd.Series | None = None,
    overlap_window_s: float = 0.0,
) -> dict:
    """Compute standardised trade statistics.

    Parameters
    ----------
    returns : pd.Series
        Signed gross returns in basis points (NaN rows should be dropped before calling).
    costs_bps : float
        Round-trip cost per trade in bps, subtracted to get net returns.
    signal_strength : pd.Series, optional
        Raw signal value aligned with returns, used to compute IC.
    holding_seconds : pd.Series, optional
        Per-trade holding time in seconds. When provided, adds avg_hold_s,
        median_hold_s, median_gross, p25_bps, p75_bps to output.
    episodes : pd.Series, optional
        Per-trade episode id. When provided, adds n_episodes (distinct ids).
    overlap_ts : pd.Series, optional
        Trade timestamps, used with overlap_window_s to count how many
        *non-overlapping* return windows were actually sampled.
    overlap_window_s : float
        Look-ahead length of one return in seconds (0 = returns do not overlap,
        e.g. EpisodeReturn). Two observations closer than this share most of
        their return path and are not independent.

    Together these give n_eff, the effective sample size, and
    t_stat_eff = t_stat * sqrt(n_eff / n). Two distinct dependencies are at
    work and both are counted (doc/overlapping-returns.md):
      - within-episode: consecutive active seconds are one event  -> n_episodes
      - horizon overlap: observations < H apart share a return    -> time blocks

    These are reported as *diagnostics*, not as the ranking key. n_eff exposes
    how few independent events back a big n (n=930k with n_eff=20k is wearing a
    costume). t_stat_eff is NOT the default rank_metric: it shrinks |t| toward
    zero, and because that shrinkage is stronger at longer horizons it reorders
    *negative* t upward, which is meaningless for selection. avg_net is
    overlap-unbiased and `stable` already gates on avg_net > 0, so ranking
    stays on t_stat; use n_eff to read significance honestly.
    """
    n = len(returns)
    nan = float("nan")

    if n == 0:
        base: dict = {
            "n": 0,
            "avg_gross": nan,
            "avg_net": nan,
            "hit_rate": nan,
            "hit_rate_net": nan,
            "std_bps": nan,
            "sharpe_per_trade": nan,
            "t_stat": nan,
            "p_value_1sided": nan,
            "p_value_bootstrap": nan,
            "skewness": nan,
            "total_net_bps": nan,
        }
        if episodes is not None:
            base["n_episodes"] = 0
        if episodes is not None or overlap_window_s > 0:
            base.update({"n_eff": 0, "t_stat_eff": nan})
        if signal_strength is not None:
            base["ic"] = nan
        if holding_seconds is not None:
            base.update(
                {
                    "avg_hold_s": nan,
                    "median_hold_s": nan,
                    "median_gross": nan,
                    "p25_bps": nan,
                    "p75_bps": nan,
                }
            )
        return base

    net = returns - costs_bps
    avg_gross = float(returns.mean())
    avg_net = float(net.mean())
    std_net = float(net.std())

    if n > 1 and std_net > 0:
        from scipy import stats as _st

        t_stat = float(avg_net / (std_net / np.sqrt(n)))
        p_value = float(_st.t.sf(t_stat, df=n - 1))
        sharpe = avg_net / std_net
    else:
        t_stat = p_value = sharpe = nan

    result: dict = {
        "n": n,
        "avg_gross": avg_gross,
        "avg_net": avg_net,
        "hit_rate": float((returns > 0).mean()),
        "hit_rate_net": float((returns > costs_bps).mean()),
        "std_bps": float(returns.std()),
        "sharpe_per_trade": sharpe,
        "t_stat": t_stat,
        "p_value_1sided": p_value,
        "p_value_bootstrap": block_bootstrap_pvalue(net.to_numpy()),
        "skewness": float(net.skew()) if n > 2 else nan,
        "total_net_bps": float(n * avg_net),
    }

    bounds = []
    if episodes is not None:
        # -1 marks "no active episode"; it is not an event.
        n_ep = int(episodes[episodes >= 0].nunique())
        result["n_episodes"] = n_ep
        bounds.append(n_ep)
    if overlap_window_s > 0 and overlap_ts is not None and len(overlap_ts):
        # How many non-overlapping windows of length H did we actually sample?
        # Observations landing in the same block overlap almost entirely.
        blocks = np.floor(
            (overlap_ts.to_numpy() - float(overlap_ts.min())) / overlap_window_s
        )
        bounds.append(int(np.unique(blocks).size))
    if bounds:
        # The tightest binding constraint: whichever dependency collapses the
        # sample furthest is the one that governs.
        n_eff = max(1, min(bounds))
        result["n_eff"] = n_eff
        result["t_stat_eff"] = (
            t_stat * float(np.sqrt(n_eff / n)) if n > 0 else nan
        )

    if signal_strength is not None:
        result["ic"] = float(signal_strength.corr(returns)) if n > 2 else nan

    if holding_seconds is not None:
        result["avg_hold_s"] = float(holding_seconds.mean())
        result["median_hold_s"] = float(holding_seconds.median())
        result["median_gross"] = float(returns.median())
        result["p25_bps"] = float(returns.quantile(0.25))
        result["p75_bps"] = float(returns.quantile(0.75))

    return result
