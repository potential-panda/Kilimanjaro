#!/usr/bin/env python3
"""Kilimanjaro research CLI.

Usage examples:
  python src/cli.py populate chart-range-sar \\
      --exchange BINANCE --symbol ETH_USDT \\
      --box-size-rate 0.004 --step 0.05 \\
      --start 2026-05-01 --end 2026-05-31
"""

import argparse
import logging
import sys
from pathlib import Path

# Make the kilimanjaro package importable when run directly as a script.
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


# ── populate chart-range-sar ─────────────────────────────────────────────────

def _cmd_populate_chart_range_sar(args: argparse.Namespace) -> int:
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
    )
    print(f"Wrote {n} flips.")
    return 0


# ── argument parser ───────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli",
        description="Kilimanjaro research CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # populate <dataset>
    pop = sub.add_parser("populate", help="Populate a dataset")
    pop_sub = pop.add_subparsers(dest="dataset", required=True)

    # populate chart-range-sar
    p = pop_sub.add_parser(
        "chart-range-sar",
        help="Populate ChartRangeSAR (Parabolic SAR flips from BINANCE range bars)",
    )
    p.add_argument("--exchange", required=True, help="Source exchange (e.g. BINANCE)")
    p.add_argument("--symbol",   required=True, help="Source symbol (e.g. ETH_USDT)")
    p.add_argument("--box-size-rate", dest="box_size_rate", type=float, required=True,
                   help="Range bar size as a fraction of price (e.g. 0.004)")
    p.add_argument("--step",         type=float, default=0.02,
                   help="Parabolic SAR step (default 0.02)")
    p.add_argument("--acceleration", type=float, default=0.02,
                   help="Parabolic SAR acceleration (default 0.02)")
    p.add_argument("--maximum",      type=float, default=0.2,
                   help="Parabolic SAR maximum (default 0.2)")
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD (inclusive)")
    p.add_argument("--end",   required=True, help="End date YYYY-MM-DD (inclusive)")
    p.add_argument("--recreate", action="store_true",
                   help="Overwrite existing daily parquet files")
    p.set_defaults(func=_cmd_populate_chart_range_sar)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
