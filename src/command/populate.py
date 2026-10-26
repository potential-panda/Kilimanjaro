import argparse


def register(sub: argparse._SubParsersAction) -> None:
    pop = sub.add_parser("populate", help="Populate a discovery dataset")
    pop_sub = pop.add_subparsers(dest="dataset", required=True)
    p = pop_sub.add_parser(
        "obi-feature",
        help="Populate OBIFeature (EMA-smoothed OBI from BINANCE BookState)",
    )
    p.add_argument("--exchange", required=True, help="Source exchange (e.g. BINANCE)")
    p.add_argument("--symbol", required=True, help="Source symbol (e.g. ETH_USDT)")
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD (inclusive)")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")
    p.add_argument(
        "--recreate", action="store_true", help="Overwrite existing daily parquet files"
    )
    p.set_defaults(func=_run_obi_feature)


def _run_obi_feature(args: argparse.Namespace) -> int:
    import logging

    from kilimanjaro.dataset.obi_feature_store import populate

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    n = populate(
        exchange=args.exchange,
        symbol=args.symbol,
        start_date=args.start,
        end_date=args.end,
        recreate=args.recreate,
    )
    print(f"Wrote {n} dates.")
    return 0
