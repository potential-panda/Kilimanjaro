"""Driver <-> ledger glue.

The ledger (`kilimanjaro.ledger`) is the machine's durable memory. The driver
records every outcome there and uses it for dedup — no separate state file.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

from kilimanjaro.ledger.core import append_event, load_events
from kilimanjaro.ledger.grid import record_grid_scan

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LEDGER_PATH = _REPO_ROOT / "cycle" / "17_ledger" / "events.jsonl"


def scan_fingerprint(spec: dict, start: str, end: str) -> str:
    """Stable id for (spec content, window). Same spec + window -> same id, so a
    re-queued identical scan is detected and skipped."""
    payload = json.dumps(
        {"spec": spec, "start": start, "end": end}, sort_keys=True, default=str
    )
    return "sf-" + hashlib.sha1(payload.encode()).hexdigest()[:16]


def already_processed(
    fingerprint: str, ledger_path: Path = DEFAULT_LEDGER_PATH
) -> bool:
    """True if any recorded event carries this scan_fingerprint."""
    for e in load_events(ledger_path):
        if (e.get("details") or {}).get("scan_fingerprint") == fingerprint:
            return True
    return False


def current_status(hypothesis_id: str, ledger_path: Path = DEFAULT_LEDGER_PATH) -> str:
    """Latest to_status recorded for this hypothesis, else 'draft'."""
    events = [
        e for e in load_events(ledger_path) if e["hypothesis_id"] == hypothesis_id
    ]
    for e in sorted(events, key=lambda e: e["created_at"], reverse=True):
        if e.get("to_status"):
            return e["to_status"]
    return "draft"


def record_status(
    hypothesis_id: str,
    to_status: str,
    actor_id: str,
    details: dict,
    from_status: str | None = None,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> dict:
    """Record an agent status_transition. from_status defaults to the current
    ledger status."""
    if from_status is None:
        from_status = current_status(hypothesis_id, ledger_path)
    return append_event(
        hypothesis_id=hypothesis_id,
        event_type="status_transition",
        actor_type="agent",
        actor_id=actor_id,
        from_status=from_status,
        to_status=to_status,
        details=details,
        ledger_path=ledger_path,
    )


def record_scan_attached(
    hypothesis_id: str,
    actor_id: str,
    scan_id: str,
    out_dir: Path,
    market: str,
    res: dict,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> dict:
    return record_grid_scan(
        hypothesis_id=hypothesis_id,
        actor_id=actor_id,
        scan_id=scan_id,
        out_dir=out_dir,
        market=market,
        res=res,
        ledger_path=ledger_path,
    )


def new_run_id(at: datetime.datetime | None = None) -> str:
    """Run-level id for a cadence run: run-YYYYMMDDTHHMMSSZ (UTC)."""
    at = at or datetime.datetime.now(datetime.UTC)
    return at.strftime("run-%Y%m%dT%H%M%SZ")


def record_cadence_digest(
    run_id: str,
    actor_id: str,
    details: dict,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> dict:
    """Append a run-level `cadence_digest` event — what a cadence run tried and
    rejected (cycles, generated, scanned, verdict mix, survivors). Not tied to a
    single hypothesis; the ledger's durable record of an unattended run."""
    return append_event(
        hypothesis_id=run_id,
        event_type="cadence_digest",
        actor_type="agent",
        actor_id=actor_id,
        details=details,
        ledger_path=ledger_path,
    )
