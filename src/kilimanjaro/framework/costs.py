"""Realistic execution cost for the screening tier.

Gap 1 of the reward function: a mid-to-mid screen that charges a flat 1 bp
happily passes ideas whose entire edge is smaller than the spread you would
actually pay (basis-follow was exactly this). To say "no" honestly, the screen
must charge the real round-trip cost of crossing the spread on the *target*
(execution) market.

For a taker who enters at the ask and exits at the bid, the round-trip cost
relative to mid is approximately the full bid-ask spread. We estimate it as the
median spread of the target market over the scan window — data-driven, so it
tracks the actual venue and period rather than a guessed constant.

This is a screening approximation. The precise, per-trade spread-aware measure is
EpisodeReturn (bid/ask at the actual fill times); this constant lets the cheap
fixed-horizon screen reject sub-spread mirages without changing its mechanism.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from kilimanjaro.data.price_cache import load_target_prices
from kilimanjaro.framework.protocol import MarketConfig

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_COST_FLOOR_BPS = 1.0  # never charge less than this, even on ultra-tight books


def estimate_round_trip_cost_bps(
    target: MarketConfig,
    start_date: str,
    end_date: str,
    data_root: Path = _DEFAULT_DATA_ROOT,
    floor_bps: float = _COST_FLOOR_BPS,
) -> float:
    """Median target bid-ask spread (bps) over the window ~= round-trip taker cost.

    Falls back to floor_bps if prices are unavailable. Uses the shared target
    price cache, so it is ~free when a scan is already loading those prices.
    """
    mp = load_target_prices(
        target.exchange, target.symbol, target.execution_size,
        start_date, end_date, data_root,
    )
    mid = mp.mid
    good = mid > 0
    if not np.any(good):
        return floor_bps
    spread_bps = (mp.ask[good] - mp.bid[good]) / mid[good] * 10000.0
    spread_bps = spread_bps[np.isfinite(spread_bps) & (spread_bps >= 0)]
    if spread_bps.size == 0:
        return floor_bps
    return max(float(np.median(spread_bps)), floor_bps)
