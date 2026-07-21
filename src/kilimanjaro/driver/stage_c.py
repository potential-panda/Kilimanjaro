"""Stage C: the forever cadence — wrap Stage B (generate) + Stage A (loop).

Each cycle tops up the queue with freshly generated specs (Stage B) and then
makes one robust pass over it (Stage A: validate → dedup → scan → verdict →
ledger → review packet). It **never crosses the human gate**: run_once parks
survivors at the review packet and never promotes — Stage C only repeats that,
it does not change the rule.

Guardrails, by design:
- A run MUST be bounded (`cycles` or `max_minutes`) — no accidental forever loop.
- `gen_per_cycle` caps how many new ideas enter per cycle.
- Dedup is inherited (generate signatures + the ledger fingerprint), so the same
  idea is never re-queued or re-scanned.
- Everything is observable: a per-cycle callback + an accumulated digest.
"""

from __future__ import annotations

import datetime
import time
from dataclasses import dataclass, field
from pathlib import Path

from kilimanjaro.driver import generate as gen
from kilimanjaro.driver.loop import run_once

_DEFAULT_RESULTS_ROOT = Path("cycle/13_results/grid_scan/expr")


@dataclass
class CycleResult:
    cycle: int
    generated: int
    scanned: int
    counts: dict
    survivors: list


@dataclass
class Digest:
    cycles: int = 0
    generated: int = 0
    scanned: int = 0
    counts: dict = field(default_factory=dict)
    survivors: list = field(default_factory=list)


def run_cadence(
    queue_dir,
    start: str,
    end: str,
    *,
    cycles: int | None = None,
    max_minutes: float | None = None,
    gen_per_cycle: int = 5,
    interval_seconds: float = 0.0,
    split: str | None = None,
    results_root: Path | None = None,
    scan_dirs: list[Path] | None = None,
    actor_id: str = "stage-c",
    dry_run: bool = False,
    on_cycle=None,
) -> Digest:
    """Run bounded generate+scan cycles. Returns an accumulated Digest.

    Requires a bound (cycles or max_minutes). Never promotes — survivors are
    parked at their review packets for a human.
    """
    if cycles is None and max_minutes is None:
        raise ValueError(
            "Stage C must be bounded — pass cycles or max_minutes "
            "(refusing to start an unbounded loop)."
        )
    queue_dir = Path(queue_dir)
    queue_dir.mkdir(parents=True, exist_ok=True)
    results_root = results_root or _DEFAULT_RESULTS_ROOT
    deadline = time.monotonic() + max_minutes * 60 if max_minutes else None

    digest = Digest()
    n = 0
    while True:
        if cycles is not None and n >= cycles:
            break
        if deadline is not None and time.monotonic() >= deadline:
            break
        n += 1
        date_str = datetime.date.today().strftime("%Y%m%d")

        # Stage B: top up the queue (dedup-guarded inside generate).
        new_paths = (
            []
            if dry_run
            else gen.generate(queue_dir, gen_per_cycle, date_str, scan_dirs=scan_dirs)
        )
        # Stage A: one robust pass over the queue.
        summary = run_once(
            queue_dir, start, end,
            actor_id=actor_id, split=split, results_root=results_root, dry_run=dry_run,
        )

        cr = CycleResult(
            cycle=n, generated=len(new_paths), scanned=summary.get("n_specs", 0),
            counts=summary.get("counts", {}), survivors=summary.get("passed", []),
        )
        digest.cycles = n
        digest.generated += cr.generated
        digest.scanned += cr.scanned
        for k, v in cr.counts.items():
            digest.counts[k] = digest.counts.get(k, 0) + v
        digest.survivors.extend(cr.survivors)
        if on_cycle:
            on_cycle(cr)

        more = (cycles is None or n < cycles) and (
            deadline is None or time.monotonic() < deadline
        )
        if more and interval_seconds:
            time.sleep(interval_seconds)

    return digest
