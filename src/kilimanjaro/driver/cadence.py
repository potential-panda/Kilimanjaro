"""The cadence — wrap the generator + the scanner loop into a forever loop.

Each cycle tops up the queue with freshly generated specs (the generator) and
then makes one robust pass over it (the scanner loop: validate → dedup → scan →
verdict → ledger → review packet). It **never crosses the human gate**: run_once
parks survivors at the review packet and never promotes — the cadence only
repeats that, it does not change the rule.

Guardrails, by design:
- A run MUST be bounded — by `cycles`, `max_minutes`, a `max_scans` cost budget,
  or a `stop_requested` signal (the daemon). No accidental forever loop.
- `gen_per_cycle` caps how many new ideas enter per cycle.
- Dedup is inherited (generate signatures + the ledger fingerprint), so the same
  idea is never re-queued or re-scanned.
- Every run is observable: a per-cycle callback, an accumulated digest, and a
  run-level `cadence_digest` event written to the ledger (what it tried/rejected).
"""

from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from kilimanjaro.driver import generate as gen
from kilimanjaro.driver import ledger_io
from kilimanjaro.driver.loop import run_once

LOG = logging.getLogger("kilimanjaro.driver.cadence")

_DEFAULT_RESULTS_ROOT = Path("cycle/13_results/grid_scan/expr")
# After a cycle raises, wait at least this long before the next one so a
# persistent failure can't hot-spin (the stop signal is still honoured during it).
_ERROR_BACKOFF_SECONDS = 5.0

# Verdicts that consumed an actual grid scan (the cost budget counts these;
# skipped-duplicate / dry-run / invalid-spec did not run a scan).
_COSTED = ("scan-passed", "scan-failed")


@dataclass
class CycleResult:
    cycle: int
    generated: int
    scanned: int
    scans_run: int
    counts: dict
    survivors: list
    error: str | None = None


@dataclass
class Digest:
    run_id: str = ""
    cycles: int = 0
    generated: int = 0
    scanned: int = 0
    scans_run: int = 0
    errors: int = 0
    counts: dict = field(default_factory=dict)
    survivors: list = field(default_factory=list)
    stop_reason: str = "completed"


def _interruptible_sleep(seconds: float, stop_requested) -> None:
    """Sleep in small chunks so a stop signal is honoured promptly (a plain
    time.sleep won't return early on a signal — PEP 475 retries)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if stop_requested is not None and stop_requested():
            return
        time.sleep(min(0.5, max(0.0, end - time.monotonic())))


def run_cadence(
    queue_dir,
    start: str,
    end: str,
    *,
    cycles: int | None = None,
    max_minutes: float | None = None,
    max_scans: int | None = None,
    gen_per_cycle: int = 5,
    interval_seconds: float = 0.0,
    split: str | None = None,
    results_root: Path | None = None,
    scan_dirs: list[Path] | None = None,
    actor_id: str = "cadence",
    dry_run: bool = False,
    stop_requested=None,
    max_consecutive_errors: int = 5,
    ledger_path: Path | None = None,
    record_digest: bool = True,
    on_cycle=None,
) -> Digest:
    """Run bounded generate+scan cycles. Returns an accumulated Digest.

    Must be bounded by at least one of `cycles`, `max_minutes`, `max_scans` (a
    cost budget = grid scans actually run), or `stop_requested` (a callable the
    daemon uses to request graceful shutdown). Never promotes — survivors are
    parked at their review packets for a human. On completion writes a run-level
    `cadence_digest` event to the ledger unless `dry_run` or `record_digest=False`.

    A failing cycle is isolated: the exception is logged and the cadence
    continues, so one bad cycle can't kill a days-long run. After
    `max_consecutive_errors` failures in a row it stops (stop_reason
    "too_many_errors") rather than spin on a persistent problem.
    """
    if (
        cycles is None
        and max_minutes is None
        and max_scans is None
        and stop_requested is None
    ):
        raise ValueError(
            "The cadence must be bounded — pass cycles, max_minutes, max_scans, or a "
            "stop_requested signal (refusing to start an unbounded loop)."
        )
    queue_dir = Path(queue_dir)
    queue_dir.mkdir(parents=True, exist_ok=True)
    results_root = results_root or _DEFAULT_RESULTS_ROOT
    deadline = time.monotonic() + max_minutes * 60 if max_minutes else None

    run_id = ledger_io.new_run_id()
    started_at = datetime.datetime.now(datetime.timezone.utc)
    digest = Digest(run_id=run_id)
    LOG.info("cadence %s started (window %s..%s)", run_id, start, end)
    n = 0
    consecutive_errors = 0
    while True:
        if stop_requested is not None and stop_requested():
            digest.stop_reason = "stopped"
            break
        if cycles is not None and n >= cycles:
            digest.stop_reason = "cycles"
            break
        if deadline is not None and time.monotonic() >= deadline:
            digest.stop_reason = "max_minutes"
            break
        if max_scans is not None and digest.scans_run >= max_scans:
            digest.stop_reason = "max_scans"
            break
        n += 1
        digest.cycles = n
        date_str = datetime.date.today().strftime("%Y%m%d")

        # One cycle is isolated: a failure in generate/run_once is logged and the
        # cadence moves on, so one bad cycle never takes down a days-long run.
        # Too many *consecutive* failures do stop it — that's a real problem.
        try:
            new_paths = (
                []
                if dry_run
                else gen.generate(queue_dir, gen_per_cycle, date_str, scan_dirs=scan_dirs)
            )
            summary = run_once(
                queue_dir,
                start,
                end,
                actor_id=actor_id,
                split=split,
                results_root=results_root,
                dry_run=dry_run,
            )
        except Exception as ex:  # noqa: BLE001 — one bad cycle must not stop the run
            consecutive_errors += 1
            digest.errors += 1
            LOG.exception("cadence cycle %d failed (%d in a row)", n, consecutive_errors)
            if on_cycle:
                on_cycle(CycleResult(n, 0, 0, 0, {}, [], error=f"{type(ex).__name__}: {ex}"))
            if consecutive_errors >= max_consecutive_errors:
                digest.stop_reason = "too_many_errors"
                LOG.error("cadence stopping: %d consecutive cycle failures", consecutive_errors)
                break
            _interruptible_sleep(max(interval_seconds, _ERROR_BACKOFF_SECONDS), stop_requested)
            continue

        consecutive_errors = 0
        counts = summary.get("counts", {})
        cost = sum(counts.get(k, 0) for k in _COSTED)
        cr = CycleResult(
            cycle=n,
            generated=len(new_paths),
            scanned=summary.get("n_specs", 0),
            scans_run=cost,
            counts=counts,
            survivors=summary.get("passed", []),
        )
        digest.generated += cr.generated
        digest.scanned += cr.scanned
        digest.scans_run += cr.scans_run
        for k, v in counts.items():
            digest.counts[k] = digest.counts.get(k, 0) + v
        digest.survivors.extend(cr.survivors)
        LOG.info(
            "cycle %d: generated=%d scanned=%d scans_run=%d survivors=%d verdicts=%s",
            n, cr.generated, cr.scanned, cr.scans_run, len(cr.survivors),
            {k: v for k, v in counts.items() if v},
        )
        if on_cycle:
            on_cycle(cr)

        more = (
            (stop_requested is None or not stop_requested())
            and (cycles is None or n < cycles)
            and (deadline is None or time.monotonic() < deadline)
            and (max_scans is None or digest.scans_run < max_scans)
        )
        if more and interval_seconds:
            _interruptible_sleep(interval_seconds, stop_requested)

    LOG.info(
        "cadence %s done (%s): %d cycles, %d scanned, %d scans, %d errors, %d survivors",
        run_id, digest.stop_reason, digest.cycles, digest.scanned, digest.scans_run,
        digest.errors, len(digest.survivors),
    )
    if record_digest and not dry_run:
        ended_at = datetime.datetime.now(datetime.timezone.utc)
        ledger_io.record_cadence_digest(
            run_id,
            actor_id,
            {
                "cycles": digest.cycles,
                "generated": digest.generated,
                "scanned": digest.scanned,
                "scans_run": digest.scans_run,
                "errors": digest.errors,
                "verdicts": digest.counts,
                "n_survivors": len(digest.survivors),
                "survivors": [
                    {"hypothesis_id": s.get("hypothesis_id"), "headline": s.get("headline")}
                    for s in digest.survivors
                ],
                "window": [start, end],
                "started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ended_at": ended_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "stop_reason": digest.stop_reason,
            },
            ledger_path=ledger_path or ledger_io.DEFAULT_LEDGER_PATH,
        )

    return digest
