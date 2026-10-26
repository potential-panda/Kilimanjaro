# Calibration

Calibration asks whether a fast discovery calculation agrees with an independent
replay for the same entry, exit, and execution mechanism. It is evidence about a
mechanism, not about an individual formula.

The machine-readable registry is
`src/kilimanjaro/framework/calibration.py`. Unknown mechanisms default to
`trend-only`; review packets must not present their absolute PnL or Sharpe as
promotion evidence.

For a new mechanism:

1. Freeze representative parameter sets and entry conditions.
2. Produce immutable discovery and replay artifacts over the same data window.
3. Compare event timing, execution prices, return direction and magnitude, and
   parameter ranking using a predeclared acceptance rule.
4. Store the evidence under `cycle/16_calibration/` and update the registry only
   after human review.

Calibration is invalidated when the mechanism, data provider, execution model,
or replay implementation changes. Package and implementation hashes in the
artifact manifest make that drift visible.
