"""`cli.py generate` — the generator: propose new formula specs into the loop queue.

Enumerates economic formula families over available markets, dedupes against
every scan-spec already on disk, and writes new specs (with fresh hypothesis ids)
into the queue for `cli.py loop` to scan.
"""

import argparse
import datetime
from pathlib import Path


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "generate",
        help="Propose new formula scan specs into the loop queue (stage B)",
    )
    p.add_argument(
        "--queue",
        default="cycle/12_specs",
        help="Where to write proposed specs (default cycle/12_specs)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max new specs to propose this call (default 5)",
    )
    p.add_argument(
        "--date",
        default=None,
        help="Date stamp for hypothesis ids YYYYMMDD (default today)",
    )
    p.add_argument(
        "--scan-dirs",
        nargs="*",
        default=["cycle/12_specs"],
        help="Dirs scanned for existing specs (dedup source)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be proposed; do not write files",
    )
    p.set_defaults(func=_run_generate)


def _run_generate(args: argparse.Namespace) -> int:
    from kilimanjaro.driver import generate as gen

    date_str = args.date or datetime.datetime.now(datetime.UTC).date().strftime(
        "%Y%m%d"
    )
    scan_dirs = [Path(d) for d in args.scan_dirs]
    seen = gen.existing_signatures(*scan_dirs)
    seq = gen.next_seq(date_str, *scan_dirs)
    # idea_ids lets a new spec join the hypothesis already covering its idea
    # instead of minting one per market — must match what driver.generate does.
    idea_ids = gen.existing_idea_ids(*scan_dirs)
    specs = gen.propose_specs(
        args.limit, date_str, seen=seen, seq_start=seq, idea_ids=idea_ids
    )

    if not specs:
        print(
            "Nothing new to propose — every viable formula is already queued or tested."
        )
        return 0

    print(f"Proposing {len(specs)} new spec(s):")
    for s in specs:
        exprs = ", ".join(e["label"] for e in s["expressions"])
        print(
            f"  {s['hypothesis_id']}  [{s['family']}]  {s['target']['exchange']}/"
            f"{s['target']['symbol']}  ({exprs})"
        )

    if args.dry_run:
        print("\n(dry-run: no files written)")
        return 0

    queue_dir = Path(args.queue)
    paths = gen.write_specs(specs, queue_dir)
    entries = gen.write_registry_entries(specs, gen.registry_dir_for(queue_dir))
    shared = len(specs) - len(entries)
    note = f", {shared} joining an existing hypothesis" if shared else ""
    print(
        f"\nWrote {len(paths)} spec(s) to {args.queue}/ and "
        f"{len(entries)} new hypothesis entry(ies){note}."
    )
    print("Run `cli.py loop` to scan them.")
    return 0
