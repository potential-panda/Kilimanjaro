"""`cli.py loop` — the autonomous scanner driver (stages 1-4).

Walks a queue of scan specs and runs each through validate -> dedup -> scan ->
verdict -> ledger, drafting a review packet for survivors. Idempotent: specs
already recorded in the ledger are skipped, so it is safe to re-run.
"""

import argparse
from pathlib import Path


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "loop",
        help="Run the autonomous scanner loop over a queue of scan specs (stages 1-4)",
    )
    p.add_argument("--queue", default="specs/queue",
                   help="Directory of scan-spec YAMLs to process (default specs/queue)")
    p.add_argument("--start", required=True, help="Loop-wide window start YYYY-MM-DD")
    p.add_argument("--end", required=True, help="Loop-wide window end YYYY-MM-DD")
    p.add_argument("--split", default=None,
                   help="Holdout split date (default: last third of the window)")
    p.add_argument("--actor-id", default="scan-loop", help="Ledger actor_id for events")
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N new specs this pass")
    p.add_argument("--results-root", default="results/grid_scan/expr",
                   help="Where scan output folders are written")
    p.add_argument("--ledger-path", default=None,
                   help="Ledger events file (default: repo ledger/events.jsonl). "
                        "Point elsewhere to isolate a test run.")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate + dedup only; do not scan or write ledger events")
    p.set_defaults(func=_run_loop)


def _run_loop(args: argparse.Namespace) -> int:
    from kilimanjaro.driver import run_once

    queue = Path(args.queue)
    if not queue.exists():
        print(f"Queue directory not found: {queue}")
        return 1

    print(f"Scanner loop over {queue}/  window {args.start} .. {args.end}"
          f"{' (dry-run)' if args.dry_run else ''}")

    def on_result(r: dict) -> None:
        tag = r["status"]
        extra = r.get("headline") or r.get("error") or ""
        print(f"  [{tag:17s}] {Path(r.get('spec_file', r.get('scan_name', '?'))).name}  {extra}")
        if r.get("review_packet"):
            print(f"      -> review packet: {r['review_packet']}")

    summary = run_once(
        queue, args.start, args.end,
        actor_id=args.actor_id, split=args.split, limit=args.limit,
        results_root=Path(args.results_root), dry_run=args.dry_run,
        ledger_path=Path(args.ledger_path) if args.ledger_path else None,
        on_result=on_result,
    )

    c = summary["counts"]
    print(f"\nDone. {summary['n_specs']} specs seen | "
          + " | ".join(f"{k}={v}" for k, v in c.items() if v))
    if summary["passed"]:
        print("\nSurvivors parked for human review:")
        for r in summary["passed"]:
            print(f"  {r['hypothesis_id']}: {r['headline']}")
            print(f"    packet: {r.get('review_packet')}")
    return 0
