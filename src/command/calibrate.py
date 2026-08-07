"""cli.py calibrate — exact-match a promoted signal's factors between Kilimanjaro
(offline: Makalu parquet feature frame) and Himalaya (online: InfluxDB measurement
TRADING, emitted by the qate strategy's add_metric per second).

    python src/cli.py calibrate --list
    python src/cli.py calibrate --start 2026-03-19 --end 2026-03-20
    python src/cli.py calibrate basis_900 --start ... --end ... --bucket <bt bucket>

Prints, per factor: pct_match, points, corr, max diff — the goal is ≈100%. For factors
below target it prints the worst mismatching seconds to drive the [match, fix] loop.
Populate the online series first by running a backtest with the factor-emitting strategy.
See kilimanjaro.calibration (registry.py = the factor list + status).
"""

from __future__ import annotations

import argparse

from kilimanjaro.calibration import registry
from kilimanjaro.calibration.registry import DEFAULT_BUCKET, MARKETS


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "calibrate",
        help="Exact-match offline (Makalu) vs online (InfluxDB) factor series",
    )
    p.add_argument("factor", nargs="?", default="all",
                   help="factor name (see --list) or 'all' (default)")
    p.add_argument("--start", help="window start YYYY-MM-DD")
    p.add_argument("--end", help="window end YYYY-MM-DD")
    p.add_argument("--bucket", default=DEFAULT_BUCKET,
                   help=f"InfluxDB bucket with the TRADING factors (default {DEFAULT_BUCKET})")
    p.add_argument("--tol", type=float, default=None,
                   help="match tolerance on the factor value (default: per-factor)")
    p.add_argument("--list", action="store_true",
                   help="list factors + calibration status and exit")
    p.add_argument("--mismatches", type=int, default=5,
                   help="worst-N mismatching seconds to print for sub-target factors")
    p.set_defaults(func=_run)


def _run(args) -> int:
    if args.list:
        print(f"{'factor':<18}{'status':<12}note")
        for name, status, note in registry.status_table():
            print(f"{name:<18}{status:<12}{note}")
        return 0

    if not (args.start and args.end):
        print("error: --start and --end are required (unless --list)")
        return 2

    names = registry.FACTOR_NAMES if args.factor == "all" else [args.factor]
    print(f"calibrate {len(names)} factor(s) | window {args.start}..{args.end} | bucket {args.bucket}")
    below = []
    for name in names:
        f = registry.load(name)
        try:
            r = f.calibrate(MARKETS, args.bucket, args.start, args.end, tol=args.tol)
        except Exception as e:  # noqa: BLE001 — report and continue the sweep
            print(f"  {name:<18} ERROR: {e}")
            continue
        print("  " + r.summary())
        if not r.ok() and r.mismatches:
            below.append((name, r))

    for name, r in below:
        print(f"\n  {name} — worst mismatches (sec, offline, online, |diff|):")
        for sec, off, on, d in r.mismatches[: args.mismatches]:
            print(f"    {sec}  {off:+.6f}  {on:+.6f}  {d:.6f}")
    if below:
        print(f"\n{len(below)}/{len(names)} factor(s) below target — inspect + fix, then re-run.")
    return 0
