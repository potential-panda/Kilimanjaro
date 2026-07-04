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
        warmup_days=args.warmup_days,
    )
    print(f"Wrote {n} flips.")
    return 0


# ── verify chart-range-sar ────────────────────────────────────────────────────

def _cmd_verify_chart_range_sar(args: argparse.Namespace) -> int:
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


# ── argument parser ───────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli",
        description="Kilimanjaro research CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── populate <dataset> ───────────────────────────────────────────────────
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
    p.add_argument("--warmup-days", dest="warmup_days", type=int, default=90,
                   help="Days of trade history to process before start to converge SAR (default: 90)")
    p.set_defaults(func=_cmd_populate_chart_range_sar)

    # ── verify <dataset> ─────────────────────────────────────────────────────
    ver = sub.add_parser("verify", help="Verify a dataset against backtest InfluxDB data")
    ver_sub = ver.add_subparsers(dest="dataset", required=True)

    # verify chart-range-sar
    v = ver_sub.add_parser(
        "chart-range-sar",
        help="Compare Kilimanjaro ChartRangeSAR bars+SAR against Himalaya backtest",
    )
    v.add_argument("--exchange", required=True, help="Source exchange (e.g. BINANCE)")
    v.add_argument("--symbol",   required=True, help="Source symbol (e.g. ETH_USDT)")
    v.add_argument("--box-size-rate", dest="box_size_rate", type=float, required=True,
                   help="Range bar size as a fraction of price (e.g. 0.004)")
    v.add_argument("--step",         type=float, default=0.02,
                   help="Parabolic SAR step (default 0.02)")
    v.add_argument("--acceleration", type=float, default=0.02,
                   help="Parabolic SAR acceleration (default 0.02)")
    v.add_argument("--maximum",      type=float, default=0.2,
                   help="Parabolic SAR maximum (default 0.2)")
    v.add_argument("--start", required=True, help="Start date YYYY-MM-DD (inclusive)")
    v.add_argument("--end",   required=True, help="End date YYYY-MM-DD (inclusive)")
    v.add_argument("--bucket", default="backtest_QASIM_TARIQ",
                   help="InfluxDB backtest bucket (default: backtest_QASIM_TARIQ)")
    v.add_argument("--param-set-id", dest="param_set_id", default=None,
                   help="Specific param_set_id tag to use (auto-detected if omitted)")
    v.add_argument("--warmup-days", dest="warmup_days", type=int, default=90,
                   help="Days of trade history to process before start to converge SAR (default: 90)")
    v.set_defaults(func=_cmd_verify_chart_range_sar)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
