"""`cli.py factory` — the cadence: repeat generate+scan (never promotes).

Repeats the generator + the scanner loop, parking survivors at their review
packets for human review, and writes a run-level `cadence_digest` to the ledger.

A bounded run stops on `--cycles`, `--max-minutes`, or `--max-scans` (a cost
budget = grid scans actually run). Or run it as a daemon (`--daemon`) for
multi-day unattended operation and stop it with `--stop` — graceful: it finishes
the current cycle, records the digest, and exits.
"""

from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("factory", help="Cadence: repeat generate+scan (never promotes)")
    p.add_argument("--start", help="Scan window start YYYY-MM-DD")
    p.add_argument("--end", help="Scan window end YYYY-MM-DD")
    p.add_argument("--queue", default="cycle/12_specs", help="Spec queue directory")
    p.add_argument("--cycles", type=int, default=None, help="Bound: number of generate+scan cycles")
    p.add_argument("--max-minutes", type=float, default=None, help="Bound: wall-clock budget")
    p.add_argument("--max-scans", type=int, default=None,
                   help="Bound: cost budget = grid scans actually run (scan-passed + scan-failed)")
    p.add_argument("--gen-per-cycle", type=int, default=5, help="New specs generated per cycle")
    p.add_argument("--interval-seconds", type=float, default=0.0, help="Sleep between cycles")
    p.add_argument("--split", default=None, help="Holdout split date (default: last third)")
    p.add_argument("--results-root", default="cycle/15_return_eval/grid_scan/expr")
    p.add_argument("--dry-run", action="store_true", help="Generate/validate only; no scan or ledger")
    p.add_argument("--daemon", action="store_true",
                   help="Run unattended until stopped (--stop); handles SIGTERM/SIGINT gracefully")
    p.add_argument("--stop", action="store_true", help="Signal a running daemon to stop, then exit")
    p.add_argument("--pid-file", default=".cadence.pid", help="Daemon PID file (default .cadence.pid)")
    p.add_argument("--log-file", default=None,
                   help="Write a rotating log here (daemon defaults to .cadence.log)")
    p.add_argument("--max-consecutive-errors", type=int, default=5,
                   help="Stop after this many failed cycles in a row (default 5)")
    p.set_defaults(func=_run_factory)


def _setup_logging(log_file: Path) -> None:
    import logging
    import logging.handlers

    logger = logging.getLogger("kilimanjaro")
    logger.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10_000_000, backupCount=5
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    logger.addHandler(handler)


def _on_cycle(cr) -> None:
    if cr.error:
        print(f"[cycle {cr.cycle}] FAILED — {cr.error} (isolated; continuing)", flush=True)
        return
    live = {k: v for k, v in cr.counts.items() if v}
    print(f"[cycle {cr.cycle}] generated={cr.generated} scanned={cr.scanned} "
          f"scans_run={cr.scans_run} verdicts={live} survivors={len(cr.survivors)}", flush=True)
    for r in cr.survivors:
        print(f"    ⤷ survivor {r['hypothesis_id']}: {r['headline']}")
        if r.get("review_packet"):
            print(f"      packet: {r['review_packet']}")


def _stop_daemon(pid_file: Path) -> int:
    if not pid_file.exists():
        print(f"No PID file at {pid_file} — nothing to stop.")
        return 1
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        print(f"PID file {pid_file} is malformed.")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to cadence daemon (pid {pid}); it stops after the current cycle.")
        return 0
    except ProcessLookupError:
        print(f"No process {pid} — removing stale PID file.")
        pid_file.unlink(missing_ok=True)
        return 1


def _run_factory(args: argparse.Namespace) -> int:
    from kilimanjaro.driver import cadence

    pid_file = Path(args.pid_file)
    if args.stop:
        return _stop_daemon(pid_file)

    if not (args.start and args.end):
        print("--start and --end are required")
        return 2

    log_file = args.log_file or (".cadence.log" if args.daemon else None)
    if log_file:
        _setup_logging(Path(log_file))
        print(f"Logging to {log_file}", flush=True)

    stop_flag = {"v": False}
    stop_requested = None
    if args.daemon:
        if pid_file.exists():
            try:
                old = int(pid_file.read_text().strip())
                os.kill(old, 0)  # exists?
                print(f"A cadence daemon already appears to be running (pid {old}). "
                      f"Stop it first (--stop) or remove {pid_file}.")
                return 2
            except (ValueError, ProcessLookupError):
                pid_file.unlink(missing_ok=True)  # stale

        def _handler(signum, frame):
            stop_flag["v"] = True

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

        def _stop_requested():
            return stop_flag["v"]

        stop_requested = _stop_requested
        pid_file.write_text(str(os.getpid()))
        print(f"cadence daemon started (pid {os.getpid()}, pid file {pid_file}). "
              "Stop with: cli.py factory --stop", flush=True)

    try:
        digest = cadence.run_cadence(
            args.queue, args.start, args.end,
            cycles=args.cycles, max_minutes=args.max_minutes, max_scans=args.max_scans,
            gen_per_cycle=args.gen_per_cycle, interval_seconds=args.interval_seconds,
            split=args.split, results_root=Path(args.results_root), dry_run=args.dry_run,
            stop_requested=stop_requested, max_consecutive_errors=args.max_consecutive_errors,
            on_cycle=_on_cycle,
        )
    except ValueError as e:
        print(f"Error: {e}")
        return 2
    finally:
        if args.daemon:
            pid_file.unlink(missing_ok=True)

    print(f"\nCadence done ({digest.stop_reason}): {digest.cycles} cycles, "
          f"{digest.generated} generated, {digest.scanned} scanned, "
          f"{digest.scans_run} grid scans run.")
    print(f"Verdicts: { {k: v for k, v in digest.counts.items() if v} }")
    print(f"Survivors parked for human review: {len(digest.survivors)} "
          "— promotion stays human; nothing was promoted.")
    for r in digest.survivors:
        print(f"  {r['hypothesis_id']}: {r['headline']}  (packet: {r.get('review_packet')})")
    if not args.dry_run:
        print(f"Ledger: cadence_digest {digest.run_id}")
    return 0
