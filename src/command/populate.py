import argparse


def register(sub: argparse._SubParsersAction) -> None:
    pop = sub.add_parser("populate", help="Populate a dataset")
    pop_sub = pop.add_subparsers(dest="dataset", required=True)
    _register_chart_range_sar(pop_sub)
    _register_obi_feature(pop_sub)


def _register_chart_range_sar(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "chart-range-sar",
        help="Populate ChartRangeSAR (Parabolic SAR flips from BINANCE range bars)",
    )
    p.add_argument("--exchange", required=True, help="Source exchange (e.g. BINANCE)")
    p.add_argument("--symbol", required=True, help="Source symbol (e.g. ETH_USDT)")
    p.add_argument(
        "--box-size-rate", dest="box_size_rate", type=float, required=True,
        help="Range bar size as a fraction of price (e.g. 0.004)",
    )
    p.add_argument("--step", type=float, default=0.02, help="Parabolic SAR step (default 0.02)")
    p.add_argument("--acceleration", type=float, default=0.02, help="Parabolic SAR acceleration (default 0.02)")
    p.add_argument("--maximum", type=float, default=0.2, help="Parabolic SAR maximum (default 0.2)")
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD (inclusive)")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")
    p.add_argument("--recreate", action="store_true", help="Overwrite existing daily parquet files")
    p.add_argument(
        "--warmup-days", dest="warmup_days", type=int, default=90,
        help="Days of trade history before start to converge SAR state (default: 90)",
    )
    p.set_defaults(func=_run_chart_range_sar)


def _register_obi_feature(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "obi-feature",
        help="Populate OBIFeature (EMA-smoothed OBI from BINANCE BookState)",
    )
    p.add_argument("--exchange", required=True, help="Source exchange (e.g. BINANCE)")
    p.add_argument("--symbol", required=True, help="Source symbol (e.g. ETH_USDT)")
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD (inclusive)")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")
    p.add_argument("--recreate", action="store_true", help="Overwrite existing daily parquet files")
    p.set_defaults(func=_run_obi_feature)


def _run_obi_feature(args: argparse.Namespace) -> int:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    from kilimanjaro.dataset.obi_feature_store import populate
    n = populate(
        exchange=args.exchange,
        symbol=args.symbol,
        start_date=args.start,
        end_date=args.end,
        recreate=args.recreate,
    )
    print(f"Wrote {n} dates.")
    return 0


def _run_chart_range_sar(args: argparse.Namespace) -> int:
    from qate.core.model import ExchangeName, Market, Symbol

    from kilimanjaro.dataset.range_sar_store import populate

    market = Market(ExchangeName[args.exchange], Symbol[args.symbol])
    n = populate(
        market=market,
        start_date=args.start,
        end_date=args.end,
        box_size_rate=args.box_size_rate,
        step=args.step,
        acceleration=args.acceleration,
        maximum=args.maximum,
        recreate=args.recreate,
        warmup_days=args.warmup_days,
    )
    print(f"Wrote {n} flips.")
    return 0
