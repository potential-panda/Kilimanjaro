#!/usr/bin/env python3
"""Kilimanjaro research CLI.

Usage examples:
  python src/cli.py populate chart-range-sar \\
      --exchange BINANCE --symbol ETH_USDT \\
      --box-size-rate 0.004 --step 0.05 \\
      --start 2026-05-01 --end 2026-05-31

  python src/cli.py verify chart-range-sar \\
      --exchange BINANCE --symbol ETH_USDT \\
      --box-size-rate 0.004 --step 0.05 \\
      --start 2026-05-01 --end 2026-05-31
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def main() -> int:
    from command import (
        blend,
        calibrate,
        factory,
        generate,
        grid,
        ledger,
        blend_corr,
        signal_corr,
        learn,
        loop,
        meta,
        populate,
        return_eval,
        verify,
        verify_scan,
    )

    parser = argparse.ArgumentParser(prog="cli", description="Kilimanjaro research CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    populate.register(sub)
    return_eval.register(sub)
    verify.register(sub)
    verify_scan.register(sub)
    grid.register(sub)
    learn.register(sub)
    blend.register(sub)
    meta.register(sub)
    loop.register(sub)
    generate.register(sub)
    ledger.register(sub)
    factory.register(sub)
    calibrate.register(sub)
    signal_corr.register(sub)
    blend_corr.register(sub)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
