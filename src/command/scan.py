import argparse

from kilimanjaro.scan.flip_sar import _DEFAULT_COSTS_BPS, SCHEDULES

_COMMON_FLIP_ARGS = [
    (
        "--flip-exchange",
        dict(required=True, help="Exchange that generates flips (e.g. BINANCE)"),
    ),
    ("--flip-symbol", dict(required=True, help="Symbol for flips (e.g. ETH_USDT)")),
    (
        "--target-exchange",
        dict(required=True, help="Exchange to measure returns on (e.g. GMO)"),
    ),
    (
        "--target-symbol",
        dict(required=True, help="Symbol to measure returns on (e.g. ETH_JPY)"),
    ),
    ("--start", dict(required=True, help="Start date YYYY-MM-DD")),
    ("--end", dict(required=True, help="End date YYYY-MM-DD")),
    (
        "--box-size-rate",
        dict(
            dest="box_size_rate",
            type=float,
            default=0.0025,
            help="Range bar size rate (e.g. 0.003)",
        ),
    ),
    (
        "--sar-step",
        dict(
            dest="sar_step",
            type=float,
            default=0.02,
            help="Parabolic SAR step (e.g. 0.04)",
        ),
    ),
    (
        "--execution-size",
        dict(
            dest="execution_size",
            default="150",
            help="MarketPrice execution size partition (e.g. 0.3 for ETH_JPY)",
        ),
    ),
]


def _add_common_flip_args(p: argparse.ArgumentParser) -> None:
    for flag, kwargs in _COMMON_FLIP_ARGS:
        p.add_argument(flag, **kwargs)


def register(sub: argparse._SubParsersAction) -> None:
    _register_scan(sub)
    _register_verify(sub)


# ── scan ─────────────────────────────────────────────────────────────────────


def _register_scan(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("scan", help="Run a flip-anchored SAR scan")
    _add_common_flip_args(p)
    p.add_argument(
        "--costs-bps",
        dest="costs_bps",
        type=float,
        default=_DEFAULT_COSTS_BPS,
        help="Round-trip cost assumption in bps (default 1.0)",
    )
    p.add_argument(
        "--schedule",
        default="",
        help=f"Named schedule preset. Choices: {', '.join(SCHEDULES)}",
    )
    p.add_argument(
        "--block-weekday-hours",
        dest="block_weekday_hours",
        default="",
        help="Comma-separated JST hours to block on weekdays (Mon–Fri).",
    )
    p.add_argument(
        "--block-weekend-hours",
        dest="block_weekend_hours",
        default="",
        help="Comma-separated JST hours to block on weekends (Sat–Sun).",
    )
    p.add_argument(
        "--exclude-jst-dates",
        dest="exclude_jst_dates",
        default="",
        help="Comma-separated JST dates (YYYY-MM-DD) to exclude.",
    )
    p.add_argument(
        "--rolling-dd-window-seconds",
        dest="rolling_dd_window_seconds",
        type=float,
        default=21600.0,
    )
    p.add_argument(
        "--rolling-dd-threshold-bps",
        dest="rolling_dd_threshold_bps",
        type=float,
        default=10.0,
    )
    p.add_argument("--out", default=None, help="Write JSON report to this path")
    p.set_defaults(func=_run_scan)


def _run_scan(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from kilimanjaro.scan.flip_sar import print_summary, scan

    if args.schedule:
        if args.schedule not in SCHEDULES:
            print(
                f"Error: unknown schedule '{args.schedule}'. Choices: {', '.join(SCHEDULES)}"
            )
            return 1
        block_weekday_hours = SCHEDULES[args.schedule]["block_weekday_hours"]
        block_weekend_hours = SCHEDULES[args.schedule]["block_weekend_hours"]
    else:
        block_weekday_hours = [
            int(h) for h in args.block_weekday_hours.split(",") if h.strip()
        ]
        block_weekend_hours = [
            int(h) for h in args.block_weekend_hours.split(",") if h.strip()
        ]

    exclude_jst_dates = [
        d.strip() for d in args.exclude_jst_dates.split(",") if d.strip()
    ]

    report = scan(
        flip_exchange=args.flip_exchange,
        flip_symbol=args.flip_symbol,
        target_exchange=args.target_exchange,
        target_symbol=args.target_symbol,
        start_date=args.start,
        end_date=args.end,
        box_size_rate=args.box_size_rate,
        sar_step=args.sar_step,
        execution_size=args.execution_size,
        costs_bps=args.costs_bps,
        block_weekday_hours=block_weekday_hours or None,
        block_weekend_hours=block_weekend_hours or None,
        exclude_jst_dates=exclude_jst_dates or None,
        rolling_dd_window_seconds=args.rolling_dd_window_seconds,
        rolling_dd_threshold_bps=args.rolling_dd_threshold_bps,
    )
    if args.schedule:
        report["schedule_name"] = args.schedule

    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_summary(report)
    text = buf.getvalue()
    print(text, end="")

    box_int  = int(round(args.box_size_rate * 100_000))
    step_int = int(round(args.sar_step * 1_000))
    sym      = args.target_symbol.replace("/", "_")
    txt_path = Path("results") / f"scan_flip_{sym}_{box_int}_{step_int}_{args.start}_{args.end}.txt"
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(text)
    print(f"Saved {txt_path}")

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
        print(f"Wrote {args.out}")
    return 0


# ── verify ────────────────────────────────────────────────────────────────────


def _register_verify(sub: argparse._SubParsersAction) -> None:
    v = sub.add_parser(
        "verify-scan",
        help="Compare scan flip prices and PnL against Himalaya backtest InfluxDB",
    )
    _add_common_flip_args(v)
    v.add_argument(
        "--bucket", default="backtest_QASIM_TARIQ", help="InfluxDB backtest bucket"
    )
    v.add_argument(
        "--param-set-id",
        dest="param_set_id",
        required=True,
        help="Backtest param_set_id tag to compare against",
    )
    v.set_defaults(func=_run_verify)


def _run_verify(args: argparse.Namespace) -> int:
    from pathlib import Path

    from kilimanjaro.scan.flip_sar import verify

    box_int = int(round(args.box_size_rate * 100_000))
    step_int = int(round(args.sar_step * 1_000))
    sym = args.target_symbol.replace("/", "_")
    out_path = (
        Path("results")
        / f"verify_flip_scan_{sym}_{box_int}_{step_int}_{args.start}_{args.end}.txt"
    )

    verify(
        flip_exchange=args.flip_exchange,
        flip_symbol=args.flip_symbol,
        target_exchange=args.target_exchange,
        target_symbol=args.target_symbol,
        start_date=args.start,
        end_date=args.end,
        box_size_rate=args.box_size_rate,
        sar_step=args.sar_step,
        execution_size=args.execution_size,
        bucket=args.bucket,
        param_set_id=args.param_set_id,
        out=out_path,
    )
    return 0
