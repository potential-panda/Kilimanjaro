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
    # Operational research commands must not load optional replay/ML integrations.
    if len(sys.argv) > 1 and sys.argv[1] == "research":
        from command import research

        parser = argparse.ArgumentParser(prog="cli")
        research.register(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args()
        return args.func(args)
    from command import (
        blend,
        blend_corr,
        calibrate,
        cont_batch,
        factory,
        generate,
        grid,
        learn,
        ledger,
        loop,
        meta,
        op_sweep,
        populate,
        research,
        return_eval,
        signal_corr,
        verify,
        verify_scan,
    )

    parser = argparse.ArgumentParser(prog="cli", description="Kilimanjaro research CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    populate.register(sub)
    research.register(sub)
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
    op_sweep.register(sub)
    cont_batch.register(sub)
    blend_corr.register(sub)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
