#!/usr/bin/env python3
"""Append to and search Kilimanjaro's research ledger (ledger/events.jsonl).

Per requirements/hypothesis-contract.md's "Duplicate Detection", finding
prior work means searching both the ledger (status/decision history) and
the hypothesis specs themselves (family, mechanism, datasets, symbols,
horizons) joined by hypothesis_id -- the ledger event format alone doesn't
carry that descriptive content, only the spec does.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

_ROOT = Path(__file__).parent.parent
_SCHEMAS_DIR = _ROOT / "schemas"
_DEFAULT_LEDGER_PATH = _ROOT / "ledger" / "events.jsonl"
_DEFAULT_SPECS_DIR = _ROOT / "specs"

_REJECTION_STATUSES = {"invalid-spec", "duplicate", "needs-tooling", "scan-failed", "retired"}
_REJECTION_DECISION_REASONS = {
    "missing-mechanism",
    "duplicate",
    "insufficient-data",
    "unsupported-feature-transform",
    "lookahead-risk",
    "timestamp-alignment-risk",
    "failed-ic-threshold",
    "failed-hit-rate-threshold",
    "failed-cost-buffer",
    "autocorrelation-adjustment-failed",
    "regime-fragile",
    "parameter-overfit",
    "latency-infeasible",
    "liquidity-infeasible",
    "human-rejected-backtest-cost",
    "backtest-overfit",
    "promotion-declined",
}


def _ledger_event_validator() -> Draft202012Validator:
    """Build a validator for ledger_event.schema.json that can resolve its
    cross-file $ref into hypothesis_spec.schema.json's $defs."""
    spec_schema = json.loads((_SCHEMAS_DIR / "hypothesis_spec.schema.json").read_text())
    event_schema = json.loads((_SCHEMAS_DIR / "ledger_event.schema.json").read_text())
    registry = Registry().with_resource(
        "hypothesis_spec.schema.json", Resource.from_contents(spec_schema)
    )
    return Draft202012Validator(event_schema, registry=registry)


def next_event_id(existing_events: list[dict], at: datetime) -> str:
    date_str = at.strftime("%Y%m%d")
    prefix = f"evt-{date_str}-"
    seqs = [int(e["event_id"][len(prefix) :]) for e in existing_events if e["event_id"].startswith(prefix)]
    return f"{prefix}{(max(seqs) + 1) if seqs else 1:03d}"


def load_events(ledger_path: Path = _DEFAULT_LEDGER_PATH) -> list[dict]:
    if not ledger_path.exists():
        return []
    events = []
    for line in ledger_path.read_text().splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def append_event(
    hypothesis_id: str,
    event_type: str,
    actor_type: str,
    actor_id: str,
    from_status: str | None = None,
    to_status: str | None = None,
    decision_reason: str | None = None,
    artifact_ids: dict | None = None,
    details: dict | None = None,
    ledger_path: Path = _DEFAULT_LEDGER_PATH,
) -> dict:
    """Validate and append one event. Returns the written event dict."""
    existing = load_events(ledger_path)
    now = datetime.now(timezone.utc)
    event = {
        "schema_version": 1,
        "event_id": next_event_id(existing, now),
        "hypothesis_id": hypothesis_id,
        "event_type": event_type,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if from_status is not None:
        event["from_status"] = from_status
    if to_status is not None:
        event["to_status"] = to_status
    if decision_reason is not None:
        event["decision_reason"] = decision_reason
    if artifact_ids is not None:
        event["artifact_ids"] = artifact_ids
    if details is not None:
        event["details"] = details

    validator = _ledger_event_validator()
    errors = [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in validator.iter_errors(event)]
    if errors:
        raise ValueError("invalid ledger event: " + "; ".join(errors))

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "a") as f:
        f.write(json.dumps(event) + "\n")
    return event


def load_specs(specs_dir: Path = _DEFAULT_SPECS_DIR) -> dict[str, dict]:
    specs = {}
    for path in sorted(specs_dir.glob("hyp-*.yaml")):
        spec = yaml.safe_load(path.read_text())
        h = spec.get("hypothesis", {})
        if h.get("id"):
            specs[h["id"]] = spec
    return specs


def _latest_status(hypothesis_id: str, spec: dict, events_by_hyp: dict[str, list[dict]]) -> str:
    events = sorted(events_by_hyp.get(hypothesis_id, []), key=lambda e: e["created_at"])
    for event in reversed(events):
        if event.get("to_status"):
            return event["to_status"]
    return spec.get("hypothesis", {}).get("status", "draft")


def search(
    family: str | None = None,
    mechanism_keyword: str | None = None,
    dataset: str | None = None,
    exchange: str | None = None,
    symbol: str | None = None,
    decision_reason: str | None = None,
    status: str | None = None,
    rejected_only: bool = False,
    specs_dir: Path = _DEFAULT_SPECS_DIR,
    ledger_path: Path = _DEFAULT_LEDGER_PATH,
) -> list[dict]:
    """Search specs+ledger jointly. Returns a list of match summaries."""
    specs = load_specs(specs_dir)
    events = load_events(ledger_path)
    events_by_hyp: dict[str, list[dict]] = {}
    for e in events:
        events_by_hyp.setdefault(e["hypothesis_id"], []).append(e)

    results = []
    for hyp_id, spec in specs.items():
        h = spec.get("hypothesis", {})
        hyp_events = events_by_hyp.get(hyp_id, [])
        decision_reasons_seen = {e["decision_reason"] for e in hyp_events if e.get("decision_reason")}
        current_status = _latest_status(hyp_id, spec, events_by_hyp)

        if family and h.get("family") != family:
            continue
        if mechanism_keyword:
            haystack = " ".join(
                [h.get("title", ""), h.get("summary", ""), h.get("expected_edge_mechanism", "")]
            ).lower()
            if mechanism_keyword.lower() not in haystack:
                continue
        if dataset:
            datasets = [d.get("dataset") for d in h.get("data_requirements", {}).get("datasets", [])]
            if dataset not in datasets:
                continue
        if exchange or symbol:
            markets = h.get("data_requirements", {}).get("markets", [])
            if exchange and not any(m.get("exchange") == exchange for m in markets):
                continue
            if symbol and not any(m.get("symbol") == symbol for m in markets):
                continue
        if decision_reason and decision_reason not in decision_reasons_seen:
            continue
        if status and current_status != status:
            continue
        is_rejected = current_status in _REJECTION_STATUSES or bool(
            decision_reasons_seen & _REJECTION_DECISION_REASONS
        )
        if rejected_only and not is_rejected:
            continue

        results.append(
            {
                "id": hyp_id,
                "parent_id": h.get("parent_id"),
                "family": h.get("family"),
                "title": h.get("title"),
                "current_status": current_status,
                "decision_reasons_seen": sorted(decision_reasons_seen),
                "rejected": is_rejected,
            }
        )
    return results


def _cmd_append(args: argparse.Namespace) -> int:
    artifact_ids = json.loads(args.artifact_ids) if args.artifact_ids else None
    details = json.loads(args.details) if args.details else None
    try:
        event = append_event(
            hypothesis_id=args.hypothesis_id,
            event_type=args.event_type,
            actor_type=args.actor_type,
            actor_id=args.actor_id,
            from_status=args.from_status,
            to_status=args.to_status,
            decision_reason=args.decision_reason,
            artifact_ids=artifact_ids,
            details=details,
        )
    except (ValueError, jsonschema.exceptions.ValidationError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(event, indent=2))
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    results = search(
        family=args.family,
        mechanism_keyword=args.mechanism,
        dataset=args.dataset,
        exchange=args.exchange,
        symbol=args.symbol,
        decision_reason=args.decision_reason,
        status=args.status,
        rejected_only=args.rejected_only,
    )
    print(json.dumps(results, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Kilimanjaro research ledger")
    sub = parser.add_subparsers(dest="command", required=True)

    p_append = sub.add_parser("append", help="Append a ledger event")
    p_append.add_argument("--hypothesis-id", required=True)
    p_append.add_argument(
        "--event-type",
        required=True,
        choices=[
            "status_transition", "scan_attached", "human_approval",
            "waiver", "correction", "threshold_change", "taxonomy_change",
        ],
    )
    p_append.add_argument("--actor-type", required=True, choices=["agent", "human"])
    p_append.add_argument("--actor-id", required=True)
    p_append.add_argument("--from-status", default=None)
    p_append.add_argument("--to-status", default=None)
    p_append.add_argument("--decision-reason", default=None)
    p_append.add_argument("--artifact-ids", default=None, help="JSON object string")
    p_append.add_argument("--details", default=None, help="JSON object string")
    p_append.set_defaults(func=_cmd_append)

    p_search = sub.add_parser("search", help="Search specs+ledger jointly")
    p_search.add_argument("--family", default=None)
    p_search.add_argument("--mechanism", default=None, help="keyword matched against title/summary/mechanism")
    p_search.add_argument("--dataset", default=None)
    p_search.add_argument("--exchange", default=None)
    p_search.add_argument("--symbol", default=None)
    p_search.add_argument("--decision-reason", default=None)
    p_search.add_argument("--status", default=None)
    p_search.add_argument("--rejected-only", action="store_true")
    p_search.set_defaults(func=_cmd_search)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
