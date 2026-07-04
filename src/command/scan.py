import argparse

from kilimanjaro.scan.flip_sar import SCHEDULES, _DEFAULT_COSTS_BPS, _DEFAULT_HORIZONS

_COMMON_FLIP_ARGS = [
    ("--flip-exchange",   dict(required=True, help="Exchange that generates flips (e.g. BINANCE)")),
    ("--flip-symbol",     dict(required=True, help="Symbol for flips (e.g. ETH_USDT)")),
    ("--target-exchange", dict(required=True, help="Exchange to measure returns on (e.g. GMO)")),
    ("--target-symbol",   dict(required=True, help="Symbol to measure returns on (e.g. ETH_JPY)")),
    ("--start",           dict(required=True, help="Start date YYYY-MM-DD")),
    ("--end",             dict(required=True, help="End date YYYY-MM-DD")),
    ("--box-size-rate",   dict(dest="box_size_rate", type=float, default=0.0025,
                               help="Range bar size rate (e.g. 0.003)")),
    ("--sar-step",        dict(dest="sar_step", type=float, default=0.02,
                               help="Parabolic SAR step (e.g. 0.04)")),
    ("--execution-size",  dict(dest="execution_size", default="150",
                               help="MarketPrice execution size partition (e.g. 0.3 for ETH_JPY)")),
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
    p.add_argument("--horizons", default=",".join(str(h) for h in _DEFAULT_HORIZONS),
                   help="Comma-separated fixed-horizon seconds")
    p.add_argument("--costs-bps", dest="costs_bps", type=float, default=_DEFAULT_COSTS_BPS,
                   help="Round-trip cost assumption in bps (default 1.0)")
    p.add_argument("--schedule", default="",
                   help=f"Named schedule preset. Choices: {', '.join(SCHEDULES)}")
    p.add_argument("--block-jst-hours", dest="block_jst_hours", default="",
                   help="Comma-separated JST hours to block on weekdays.")
    p.add_argument("--block-sat-jst-hours", dest="block_sat_jst_hours", default="",
                   help="Comma-separated JST hours to block on Saturdays.")
    p.add_argument("--exclude-jst-dates", dest="exclude_jst_dates", default="",
                   help="Comma-separated JST dates (YYYY-MM-DD) to exclude.")
    p.add_argument("--rolling-dd-window-seconds", dest="rolling_dd_window_seconds",
                   type=float, default=0.0)
    p.add_argument("--rolling-dd-threshold-bps", dest="rolling_dd_threshold_bps",
                   type=float, default=0.0)
    p.add_argument("--out", default=None, help="Write JSON report to this path")
    p.set_defaults(func=_run_scan)


def _run_scan(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from kilimanjaro.scan.flip_sar import SCHEDULES, print_summary, scan

    if args.schedule:
        if args.schedule not in SCHEDULES:
            print(f"Error: unknown schedule '{args.schedule}'. Choices: {', '.join(SCHEDULES)}")
            return 1
        block_jst_hours     = SCHEDULES[args.schedule]["block_jst_hours"]
        block_sat_jst_hours = SCHEDULES[args.schedule]["block_sat_jst_hours"]
    else:
        block_jst_hours = [int(h) for h in args.block_jst_hours.split(",") if h.strip()]
        block_sat_jst_hours = [int(h) for h in args.block_sat_jst_hours.split(",") if h.strip()]

    exclude_jst_dates = [d.strip() for d in args.exclude_jst_dates.split(",") if d.strip()]

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


# ── verify ────────────────────────────────────────────────────────────────────

def _register_verify(sub: argparse._SubParsersAction) -> None:
    v = sub.add_parser(
        "verify-scan",
        help="Compare scan flip prices and PnL against Himalaya backtest InfluxDB",
    )
    _add_common_flip_args(v)
    v.add_argument("--bucket", default="backtest_QASIM_TARIQ",
                   help="InfluxDB backtest bucket")
    v.add_argument("--param-set-id", dest="param_set_id", required=True,
                   help="Backtest param_set_id tag to compare against")
    v.set_defaults(func=_run_verify)


def _run_verify(args: argparse.Namespace) -> int:
    from kilimanjaro.scan.flip_sar import verify

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
    )
    return 0
