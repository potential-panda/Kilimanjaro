#!/usr/bin/env python3
"""Ledger update workflow (task 9): the glue between validate_spec.py,
scanner.py, threshold_evaluator.py, and ledger.py -- runs a spec through
the full pipeline and records the outcome as one ledger event.

This is what makes the loop cumulative across sessions: every spec that
passes validation gets exactly one status_transition event recorded,
whatever the verdict, with the scan report attached by id. A later
session's duplicate search (tools/ledger.py's search()) finds it whether
it passed, failed, or needs more data -- nothing here ever writes a
status beyond scan-passed/scan-failed/needs-more-data/needs-tooling.
Promoting a scan-passed hypothesis to backtest-approved is a separate,
explicit human action (a different event_type, human_approval) that this
workflow never performs on its own.

An invalid spec is refused outright -- it never reaches the scanner or the
ledger, since there'd be nothing meaningful to record.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from ledger import append_event, current_status  # noqa: E402
from scanner import run_scan, write_report  # noqa: E402
from threshold_evaluator import evaluate  # noqa: E402
from validate_spec import validate  # noqa: E402

_ROOT = Path(__file__).parent.parent
_DEFAULT_LEDGER_PATH = _ROOT / "ledger" / "events.jsonl"
_DEFAULT_SCAN_REPORTS_DIR = _ROOT / "scan-reports"

# threshold_verdict.status -> hypothesis lifecycle status. Deliberately has
# no entry that reaches past scan-passed -- backtest-approved only ever
# comes from a separate human_approval event.
_STATUS_MAP = {
    "pass": "scan-passed",
    "fail": "scan-failed",
    "needs-more-data": "needs-more-data",
    "needs-tooling": "needs-tooling",
}


class WorkflowError(Exception):
    pass


def run_scan_workflow(
    spec_path: str | Path,
    start_date: str,
    end_date: str,
    actor_type: str = "agent",
    actor_id: str = "scanner-v1",
    scan_reports_dir: Path = _DEFAULT_SCAN_REPORTS_DIR,
    ledger_path: Path = _DEFAULT_LEDGER_PATH,
) -> dict:
    spec = yaml.safe_load(Path(spec_path).read_text())

    spec_errors = validate(spec)
    if spec_errors:
        raise WorkflowError(f"spec is invalid, refusing to scan: {spec_errors}")

    h = spec["hypothesis"]
    hypothesis_id = h["id"]

    report = run_scan(spec, start_date, end_date)
    verdict = evaluate(report, spec)
    report["threshold_verdict"] = verdict
    report_path = write_report(report, scan_reports_dir)

    from_status = current_status(hypothesis_id, spec, ledger_path)
    to_status = _STATUS_MAP[verdict["status"]]
    decision_reason = verdict["failed_rules"][0] if verdict["failed_rules"] else None

    event = append_event(
        hypothesis_id=hypothesis_id,
        event_type="status_transition",
        actor_type=actor_type,
        actor_id=actor_id,
        from_status=from_status,
        to_status=to_status,
        decision_reason=decision_reason,
        artifact_ids={"spec_id": hypothesis_id, "scan_report_id": report["id"]},
        details={
            "threshold_status": verdict["status"],
            "yellow_flags": verdict["yellow_flags"],
            "verdict_text": verdict["verdict_text"],
        },
        ledger_path=ledger_path,
    )

    return {"report_path": str(report_path), "report": report, "ledger_event": event}


def main() -> int:
    import json

    parser = argparse.ArgumentParser(description="Run a spec through scan -> evaluate -> ledger")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--actor-type", default="agent", choices=["agent", "human"])
    parser.add_argument("--actor-id", default="scanner-v1")
    args = parser.parse_args()

    try:
        result = run_scan_workflow(args.spec, args.start, args.end, args.actor_type, args.actor_id)
    except WorkflowError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Scan report: {result['report_path']}")
    print(f"Verdict: {result['report']['threshold_verdict']['status']}")
    print(f"Ledger event: {json.dumps(result['ledger_event'], indent=2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
