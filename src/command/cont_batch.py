"""cli.py cont-batch — batch the continuous-barrier grid (direction x clock x dead band).

Shares one feature-frame load across every cell, so a wide grid is practical. Each
cell still runs through the same stage-15 pieces (schedule, drawdown gate,
BetweenSignalReturn) as a single `return-eval`, and carries se/t so thin cells cannot
masquerade as winners.

    python src/cli.py cont-batch --config cycle/12_specs/probes/cont_grid_btc.yaml \
        --start 2025-08-01 --end 2026-02-28
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from kilimanjaro.driver.cont_batch import run_batch

_RESULTS = Path("cycle/15_return_eval")


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("cont-batch", help="Batch the continuous-barrier grid")
    p.add_argument("--config", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--cost-bps", type=float, default=1.0)
    p.add_argument("--out", default=None)
    p.set_defaults(func=_run)


def _run(args) -> int:
    cfg = yaml.safe_load(Path(args.config).read_text())
    df = run_batch(cfg, args.start, args.end, cost_bps=args.cost_bps)
    out = (
        Path(args.out)
        if args.out
        else _RESULTS / f"cont_batch_{Path(args.config).stem}"
    )
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "grid.csv", index=False)
    if len(df):
        # Bar for a grid of N cells: the largest |t| pure noise yields is ~sqrt(2 ln N).
        import numpy as np

        bar = float(np.sqrt(2 * np.log(max(len(df), 2))))
        print(
            f"\n  {len(df):,} cells ran | noise-max |t| for this grid size = {bar:.2f}"
        )
        print("\n  ── top 20 by net_avg_bps ──")
        print(
            f"  {'direction':<18}{'clock':>7}{'param':>10}{'band':>6}{'nTr':>7}"
            f"{'netAvg':>8}{'se':>7}{'t':>7}{'bars/tr':>8}{'holdS':>8}"
        )
        for _, r in df.head(20).iterrows():
            print(
                f"  {r.direction:<18}{r.clock:>7}{r.param:>10.6g}{r.dead_band:>6.1f}"
                f"{int(r.n_trades):>7}{r.net_avg_bps:>+8.2f}{r.se:>7.2f}{r.t:>7.2f}"
                f"{r.bars_per_trade:>8.1f}{int(r.avg_hold_s):>8}"
            )
        sig = df[(df.net_avg_bps > 0) & (df.t > bar) & (df.n_trades >= 500)]
        print(f"\n  cells passing net>0 AND |t|>{bar:.2f} AND n>=500: {len(sig)}")
    print(f"  wrote {out}/grid.csv")
    return 0
