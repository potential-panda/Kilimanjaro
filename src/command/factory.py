"""`cli.py factory` — Stage C: bounded generate+scan cadence (never promotes).

Repeats Stage B (generate) + Stage A (loop) for a bounded number of cycles or
minutes, parking any survivors at their review packets for human review.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("factory", help="Stage C: bounded generate+scan cadence (never promotes)")
    p.add_argument("--start", required=True, help="Scan window start YYYY-MM-DD")
    p.add_argument("--end", required=True, help="Scan window end YYYY-MM-DD")
    p.add_argument("--queue", default="cycle/12_specs/queue", help="Spec queue directory")
    p.add_argument("--cycles", type=int, default=None, help="Number of generate+scan cycles (a bound)")
    p.add_argument("--max-minutes", type=float, default=None, help="Wall-clock budget (a bound)")
    p.add_argument("--gen-per-cycle", type=int, default=5, help="New specs generated per cycle")
    p.add_argument("--interval-seconds", type=float, default=0.0, help="Sleep between cycles")
    p.add_argument("--split", default=None, help="Holdout split date (default: last third)")
    p.add_argument("--results-root", default="cycle/13_results/grid_scan/expr")
    p.add_argument("--dry-run", action="store_true", help="Generate/validate only; no scan or ledger")
    p.set_defaults(func=_run_factory)


def _run_factory(args: argparse.Namespace) -> int:
    from kilimanjaro.driver import stage_c

    def on_cycle(cr) -> None:
        live = {k: v for k, v in cr.counts.items() if v}
        print(f"[cycle {cr.cycle}] generated={cr.generated} scanned={cr.scanned} "
              f"verdicts={live} survivors={len(cr.survivors)}", flush=True)
        for r in cr.survivors:
            print(f"    ⤷ survivor {r['hypothesis_id']}: {r['headline']}")
            if r.get("review_packet"):
                print(f"      packet: {r['review_packet']}")

    try:
        digest = stage_c.run_cadence(
            args.queue, args.start, args.end,
            cycles=args.cycles, max_minutes=args.max_minutes,
            gen_per_cycle=args.gen_per_cycle, interval_seconds=args.interval_seconds,
            split=args.split, results_root=Path(args.results_root), dry_run=args.dry_run,
            on_cycle=on_cycle,
        )
    except ValueError as e:
        print(f"Error: {e}")
        return 2

    print(f"\nStage C done: {digest.cycles} cycles, {digest.generated} specs generated, "
          f"{digest.scanned} scanned.")
    print(f"Verdicts: { {k: v for k, v in digest.counts.items() if v} }")
    print(f"Survivors parked for human review: {len(digest.survivors)} "
          "— promotion stays human; nothing was promoted.")
    for r in digest.survivors:
        print(f"  {r['hypothesis_id']}: {r['headline']}  (packet: {r.get('review_packet')})")
    return 0
