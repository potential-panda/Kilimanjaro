"""Driver <-> ledger glue.

The ledger lives in tools/ (ledger.py, grid_ledger.py) and is the machine's
durable memory. The driver records every outcome there and uses it for dedup —
no separate state file. This module is the one place that reaches into tools/,
keeping that coupling localized.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOLS = _REPO_ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from grid_ledger import record_grid_scan  # noqa: E402
from ledger import append_event, load_events  # noqa: E402

DEFAULT_LEDGER_PATH = _REPO_ROOT / "ledger" / "events.jsonl"


def scan_fingerprint(spec: dict, start: str, end: str) -> str:
    """Stable id for (spec content, window). Same spec + window -> same id, so a
    re-queued identical scan is detected and skipped."""
    payload = json.dumps({"spec": spec, "start": start, "end": end},
                         sort_keys=True, default=str)
    return "sf-" + hashlib.sha1(payload.encode()).hexdigest()[:16]


def already_processed(fingerprint: str, ledger_path: Path = DEFAULT_LEDGER_PATH) -> bool:
    """True if any recorded event carries this scan_fingerprint."""
    for e in load_events(ledger_path):
        if (e.get("details") or {}).get("scan_fingerprint") == fingerprint:
            return True
    return False


def current_status(hypothesis_id: str, ledger_path: Path = DEFAULT_LEDGER_PATH) -> str:
    """Latest to_status recorded for this hypothesis, else 'draft'."""
    events = [e for e in load_events(ledger_path) if e["hypothesis_id"] == hypothesis_id]
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
        hypothesis_id=hypothesis_id, actor_id=actor_id, scan_id=scan_id,
        out_dir=out_dir, market=market, res=res, ledger_path=ledger_path,
    )
