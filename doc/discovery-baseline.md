# Continuous discovery baseline

Audit date: 2026-10-26. Base revision: `d91ffa5834a0917b8eb2077a1c19e5bde498ec67`.

Python 3.13.9; pytest 9.0.1; pandas 2.3.3; pyarrow 22.0.0; DuckDB 1.4.4;
Hase 0.8.0; Komachi 0.8.0; Makalu 0.1.0; qate 0.5.0.
Hase and Komachi are editable installs under `workspace/KamakuraQuantLab/`.
The shared lake resolves through Hase/Komachi; explicit roots bypass that lookup.

Initial `python -m pytest -q`: 224 passed, 13 skipped, five failures (four
subtests in expression validation and one maker-return test coupled to lake data).
Initial `python src/cli.py --help` failed because grid registration referenced
removed drawdown constants. These failures are addressed with the M0 changes.
After those fixes, the existing suite reports 225 passed, 13 skipped, 24 subtests
passed. New recovery/isolation regression coverage is in `test_discovery_baseline.py`.

Confirmed retry bug: a `scan-ready` ledger event suppressed retry after failure.
Dedup now recognizes terminal scan results; review packets publish before terminal
success. Library defaults resolve the common root at call time, preserving explicit
temporary roots. Invalid expression syntax consistently raises the documented
ValueError. Long-only maker evaluation returns without reading prices when all
signals are ineligible. CLI gate defaults use their canonical module.

Calibration is split between `framework/calibration.py` (old mechanism statuses)
and `calibration/` (newer factor and aquila v1/v3 trade comparison adapters).
The old assertion that expression replay is universally unavailable is stale.
Neither a registry label nor an import establishes a version-scoped certificate;
M6 must bind actual comparison evidence to the mechanism and implementation.

Current Hase supports MarketPrice, Spread, Volatility, and its own BookState schema.
Makalu also implements BookState, BookChange and TradeAggression. BookState schemas
are not interchangeable: resolve a provider and exact required columns explicitly.
Makalu's pipeline API accepts an explicit ParquetStore; its populate CLI reads its
configured root. Research adapters must use isolated roots and verify outputs.
