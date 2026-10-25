import argparse


def register(sub: argparse._SubParsersAction) -> None:
    ver = sub.add_parser(
        "verify", help="Verify a dataset against backtest InfluxDB data"
    )
    ver_sub = ver.add_subparsers(dest="dataset", required=True)
    _register_chart_range_sar(ver_sub)


def _register_chart_range_sar(sub: argparse._SubParsersAction) -> None:
    v = sub.add_parser(
        "chart-range-sar",
        help="Compare Kilimanjaro ChartRangeSAR bars+SAR against Himalaya backtest",
    )
    v.add_argument("--exchange", required=True, help="Source exchange (e.g. BINANCE)")
    v.add_argument("--symbol", required=True, help="Source symbol (e.g. ETH_USDT)")
    v.add_argument(
        "--box-size-rate",
        dest="box_size_rate",
        type=float,
        required=True,
        help="Range bar size as a fraction of price (e.g. 0.004)",
    )
    v.add_argument(
        "--step", type=float, default=0.02, help="Parabolic SAR step (default 0.02)"
    )
    v.add_argument(
        "--acceleration",
        type=float,
        default=0.02,
        help="Parabolic SAR acceleration (default 0.02)",
    )
    v.add_argument(
        "--maximum", type=float, default=0.2, help="Parabolic SAR maximum (default 0.2)"
    )
    v.add_argument("--start", required=True, help="Start date YYYY-MM-DD (inclusive)")
    v.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")
    v.add_argument(
        "--bucket",
        default="backtest_QASIM_TARIQ",
        help="InfluxDB backtest bucket (default: backtest_QASIM_TARIQ)",
    )
    v.add_argument(
        "--param-set-id",
        dest="param_set_id",
        default=None,
        help="Specific param_set_id tag to use (auto-detected if omitted)",
    )
    v.add_argument(
        "--warmup-days",
        dest="warmup_days",
        type=int,
        default=90,
        help="Days of trade history before start to converge SAR state (default: 90)",
    )
    v.set_defaults(func=_run_chart_range_sar)


def _run_chart_range_sar(args: argparse.Namespace) -> int:
    from qate.core.model import ExchangeName, Market, Symbol

    from kilimanjaro.dataset.range_sar_store import verify

    market = Market(ExchangeName[args.exchange], Symbol[args.symbol])
    verify(
        market=market,
        start_date=args.start,
        end_date=args.end,
        box_size_rate=args.box_size_rate,
        step=args.step,
        acceleration=args.acceleration,
        maximum=args.maximum,
        bucket=args.bucket,
        param_set_id=args.param_set_id,
        warmup_days=args.warmup_days,
    )
    return 0
