"""Execution cost policy for Kilimanjaro scans — one number, one source.

BASE POLICY (do not deviate without changing this file and the docs):

1. Every scan return calc executes at the **actual bid/ask** at the first tick
   after the signal — never mid. BUY enters at ask / exits at bid; SELL the
   reverse. So the **bid-ask spread is already paid inside every scan return**.
2. Therefore we do **NOT** estimate or add a separate spread cost. Charging the
   spread on top of a bid/ask fill double-counts it (this bug cost us a day —
   it made every combo look unprofitable at "realistic cost").
3. The only cost added on top is **latency slippage**: the ~100 ms round-trip to
   the exchange order API moves the fill against us. Production experience puts
   this at **0.5 bps per order → 1.0 bps per round-trip PnL** (enter + exit).

So the single cost knob for any scan is a flat ``ROUND_TRIP_COST_BPS`` applied
per completed trade, on top of spread-inclusive bid/ask returns. This is a
central-estimate of the *mean* slippage; it does not model slippage *variance*
(which spikes in volatile windows and deepens realized drawdowns) — for that,
trust the Enoshima backtest, not the screen (see doc/calibration.md).
"""

from __future__ import annotations

# Per-order latency slippage from production trading (0.5 bps/order).
PER_ORDER_SLIPPAGE_BPS = 0.5

# Round-trip cost charged per completed trade in every scan: enter + exit.
# This is THE cost. Do not add spread — bid/ask execution already includes it.
ROUND_TRIP_COST_BPS = 2 * PER_ORDER_SLIPPAGE_BPS  # = 1.0


def round_trip_cost_bps() -> float:
    """The canonical per-trade scan cost (bps). See module docstring / policy."""
    return ROUND_TRIP_COST_BPS
