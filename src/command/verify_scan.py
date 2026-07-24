"""`cli.py verify-scan` — the scan↔backtest fidelity check.

Backs onto the backtest data source (`kilimanjaro.dataset.backtest.verify`).
Running scans themselves is `cli.py grid {flip,horizon}` — there is no
single-config scan command; a scan is always a grid run.
"""

import argparse

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
    v = sub.add_parser(
        "verify-scan",
        help="Compare a flip scan's prices and PnL against the Himalaya backtest InfluxDB",
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

    from kilimanjaro.dataset import backtest

    box_int = int(round(args.box_size_rate * 100_000))
    step_int = int(round(args.sar_step * 1_000))
    sym = args.target_symbol.replace("/", "_")
    out_path = (
        Path("cycle/13_results")
        / f"verify_scan_{sym}_{box_int}_{step_int}_{args.start}_{args.end}.txt"
    )

    backtest.verify(
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
