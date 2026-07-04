#!/usr/bin/env python3
"""Thin shim — delegates to kilimanjaro.scan.flip_sar via src/cli.py scan."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.scan.flip_sar import SCHEDULES, _DEFAULT_COSTS_BPS, _DEFAULT_HORIZONS  # noqa: F401 (re-exported for callers)
from kilimanjaro.scan.flip_sar import print_summary, run_flip_scan  # noqa: F401

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Flip-anchored scan for event-driven signals")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="Run a flip-anchored scan")
    p.add_argument("--flip-exchange",   required=True)
    p.add_argument("--flip-symbol",     required=True)
    p.add_argument("--target-exchange", required=True)
    p.add_argument("--target-symbol",   required=True)
    p.add_argument("--start",           required=True)
    p.add_argument("--end",             required=True)
    p.add_argument("--box-size-rate",    dest="box_size_rate", type=float, default=0.0025)
    p.add_argument("--sar-step",         dest="sar_step",      type=float, default=0.02)
    p.add_argument("--execution-size",   dest="execution_size", default="150")
    p.add_argument("--horizons",        default=",".join(str(h) for h in _DEFAULT_HORIZONS))
    p.add_argument("--costs-bps",       dest="costs_bps", type=float, default=_DEFAULT_COSTS_BPS)
    p.add_argument("--schedule",        default="")
    p.add_argument("--block-jst-hours",     dest="block_jst_hours",     default="")
    p.add_argument("--block-sat-jst-hours", dest="block_sat_jst_hours", default="")
    p.add_argument("--exclude-jst-dates",   dest="exclude_jst_dates",   default="")
    p.add_argument("--rolling-dd-window-seconds", dest="rolling_dd_window_seconds", type=float, default=0.0)
    p.add_argument("--rolling-dd-threshold-bps",  dest="rolling_dd_threshold_bps",  type=float, default=0.0)
    p.add_argument("--out", default=None)

    args = parser.parse_args()

    if args.schedule:
        preset = SCHEDULES[args.schedule]
        block_jst_hours     = preset["block_jst_hours"]
        block_sat_jst_hours = preset["block_sat_jst_hours"]
    else:
        block_jst_hours = [int(h) for h in args.block_jst_hours.split(",") if h.strip()]
        block_sat_jst_hours = [int(h) for h in args.block_sat_jst_hours.split(",") if h.strip()]

    exclude_jst_dates = [d.strip() for d in args.exclude_jst_dates.split(",") if d.strip()]

    report = run_flip_scan(
        flip_exchange=args.flip_exchange,
        flip_symbol=args.flip_symbol,
        target_exchange=args.target_exchange,
        target_symbol=args.target_symbol,
        start_date=args.start,
        end_date=args.end,
        box_size_rate=args.box_size_rate,
        sar_step=args.sar_step,
        execution_size=args.execution_size,
        horizons=[int(h) for h in args.horizons.split(",")],
        costs_bps=args.costs_bps,
        block_jst_hours=block_jst_hours or None,
        block_sat_jst_hours=block_sat_jst_hours or None,
        exclude_jst_dates=exclude_jst_dates or None,
        rolling_dd_window_seconds=args.rolling_dd_window_seconds or None,
        rolling_dd_threshold_bps=args.rolling_dd_threshold_bps or None,
    )
    if args.schedule:
        report["schedule_name"] = args.schedule
    print_summary(report)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
        print(f"Wrote {args.out}")
