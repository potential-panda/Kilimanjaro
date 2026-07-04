import argparse

from kilimanjaro.scan.flip_sar import SCHEDULES, _DEFAULT_COSTS_BPS, _DEFAULT_HORIZONS


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("scan", help="Run a flip-anchored SAR scan")
    p.add_argument("--flip-exchange",   required=True, help="Exchange that generates flips (e.g. BINANCE)")
    p.add_argument("--flip-symbol",     required=True, help="Symbol for flips (e.g. ETH_USDT)")
    p.add_argument("--target-exchange", required=True, help="Exchange to measure returns on (e.g. GMO)")
    p.add_argument("--target-symbol",   required=True, help="Symbol to measure returns on (e.g. ETH_JPY)")
    p.add_argument("--start",           required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end",             required=True, help="End date YYYY-MM-DD")
    p.add_argument("--box-size-rate",   dest="box_size_rate", type=float, default=0.0025,
                   help="Range bar size rate (e.g. 0.004)")
    p.add_argument("--sar-step",        dest="sar_step", type=float, default=0.02,
                   help="Parabolic SAR step/acceleration (e.g. 0.02 or 0.05)")
    p.add_argument("--execution-size",  dest="execution_size", default="150",
                   help="MarketPrice execution size partition (e.g. 150 for XRP_JPY, 0.1 for ETH_JPY)")
    p.add_argument("--horizons",        default=",".join(str(h) for h in _DEFAULT_HORIZONS),
                   help="Comma-separated fixed-horizon seconds")
    p.add_argument("--costs-bps",       dest="costs_bps", type=float, default=_DEFAULT_COSTS_BPS,
                   help="Round-trip cost assumption in bps (default 1.0)")
    p.add_argument("--schedule",        default="",
                   help=f"Named schedule preset. Choices: {', '.join(SCHEDULES)}")
    p.add_argument("--block-jst-hours", dest="block_jst_hours", default="",
                   help="Comma-separated JST hours to block on weekdays. Ignored when --schedule is set.")
    p.add_argument("--block-sat-jst-hours", dest="block_sat_jst_hours", default="",
                   help="Comma-separated JST hours to block on Saturdays. Ignored when --schedule is set.")
    p.add_argument("--exclude-jst-dates", dest="exclude_jst_dates", default="",
                   help="Comma-separated JST dates (YYYY-MM-DD) to exclude entirely.")
    p.add_argument("--rolling-dd-window-seconds", dest="rolling_dd_window_seconds",
                   type=float, default=0.0,
                   help="Window in seconds for rolling peak-drawdown gate (0 to disable)")
    p.add_argument("--rolling-dd-threshold-bps", dest="rolling_dd_threshold_bps",
                   type=float, default=0.0,
                   help="Block when cum PnL drops this many bps below rolling peak (0 to disable)")
    p.add_argument("--out", default=None, help="Write JSON report to this path")
    p.set_defaults(func=_run_scan)


def _run_scan(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from kilimanjaro.scan.flip_sar import SCHEDULES, print_summary, run_flip_scan

    if args.schedule:
        if args.schedule not in SCHEDULES:
            print(f"Error: unknown schedule '{args.schedule}'. Choices: {', '.join(SCHEDULES)}")
            return 1
        preset = SCHEDULES[args.schedule]
        block_jst_hours     = preset["block_jst_hours"]
        block_sat_jst_hours = preset["block_sat_jst_hours"]
    else:
        block_jst_hours = (
            [int(h) for h in args.block_jst_hours.split(",") if h.strip()]
            if args.block_jst_hours else []
        )
        block_sat_jst_hours = (
            [int(h) for h in args.block_sat_jst_hours.split(",") if h.strip()]
            if args.block_sat_jst_hours else []
        )

    exclude_jst_dates = (
        [d.strip() for d in args.exclude_jst_dates.split(",") if d.strip()]
        if args.exclude_jst_dates else []
    )

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

    return 0
