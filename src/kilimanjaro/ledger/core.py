#!/usr/bin/env python3
"""Append to and search Kilimanjaro's research ledger (cycle/17_ledger/events.jsonl).

Per requirements/hypothesis-contract.md's "Duplicate Detection", finding
prior work means searching both the ledger (status/decision history) and
the hypothesis specs themselves (family, mechanism, datasets, symbols,
horizons) joined by hypothesis_id -- the ledger event format alone doesn't
carry that descriptive content, only the spec does.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource


def _find_repo_root(start: Path) -> Path:
    """Walk up to the repo root (the dir holding pyproject.toml + src/)."""
    for p in (start, *start.parents):
        if (p / "pyproject.toml").is_file() and (p / "src").is_dir():
            return p
    return start.parents[3]  # fallback: src/kilimanjaro/ledger/core.py -> root


_ROOT = _find_repo_root(Path(__file__).resolve())
_CYCLE = _ROOT / "cycle"
_SCHEMAS_DIR = _CYCLE / "01_schemas"
_DEFAULT_LEDGER_PATH = _CYCLE / "17_ledger" / "events.jsonl"
_DEFAULT_HYPOTHESES_DIR = _CYCLE / "11_hypotheses"

_REJECTION_STATUSES = {
    "invalid-spec",
    "duplicate",
    "needs-tooling",
    "scan-failed",
    "retired",
}
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
    seqs = [
        int(e["event_id"][len(prefix) :])
        for e in existing_events
        if e["event_id"].startswith(prefix) and e["event_id"][len(prefix) :].isdigit()
    ]
    return f"{prefix}{(max(seqs) + 1) if seqs else 1:03d}"


def load_events(ledger_path: Path = _DEFAULT_LEDGER_PATH) -> list[dict]:
    if not ledger_path.exists():
        return []
    with ledger_path.open() as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        events = []
        lines = stream.readlines()
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                if i != len(lines) - 1 or line.endswith("\n"):
                    raise
                # A killed append may leave a torn final line. The exclusive
                # writer archives and repairs it before replaying the outbox.
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
    event_id: str | None = None,
    created_at: str | None = None,
) -> dict:
    """Validate and append one event. Returns the written event dict."""
    now = datetime.now(UTC)
    event = {
        "schema_version": 1,
        "event_id": event_id,
        "hypothesis_id": hypothesis_id,
        "event_type": event_type,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "created_at": created_at or now.strftime("%Y-%m-%dT%H:%M:%SZ"),
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

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        lines = stream.readlines()
        existing = []
        good_end = 0
        for i, line in enumerate(lines):
            try:
                if line.strip():
                    existing.append(json.loads(line))
            except json.JSONDecodeError:
                if i != len(lines) - 1 or line.endswith("\n"):
                    raise
                from kilimanjaro.research.identity import identity

                archive = ledger_path.with_name(
                    ledger_path.name + ".torn-" + identity("tail", line)
                )
                with archive.open("a") as saved:
                    if saved.tell() == 0:
                        saved.write(line)
                        saved.flush()
                        os.fsync(saved.fileno())
                stream.seek(good_end)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
                break
            good_end += len(line.encode())
        event["event_id"] = event_id or next_event_id(existing, now)
        for old in existing:
            if old["event_id"] == event["event_id"]:
                if old != event:
                    raise ValueError(
                        "Conflicting content for an existing ledger event id"
                    )
                return old
        errors = [e.message for e in _ledger_event_validator().iter_errors(event)]
        if errors:
            raise ValueError("invalid ledger event: " + "; ".join(errors))
        stream.seek(0, os.SEEK_END)
        if (
            existing
            and lines
            and not lines[-1].endswith("\n")
            and good_end == stream.tell()
        ):
            stream.write("\n")
        stream.write(json.dumps(event) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return event


def load_hypotheses(hypotheses_dir: Path = _DEFAULT_HYPOTHESES_DIR) -> dict[str, dict]:
    specs = {}
    for path in sorted(hypotheses_dir.glob("hyp-*.yaml")):
        spec = yaml.safe_load(path.read_text())
        h = spec.get("hypothesis", {})
        if h.get("id"):
            specs[h["id"]] = spec
    return specs


def _latest_status(
    hypothesis_id: str, spec: dict, events_by_hyp: dict[str, list[dict]]
) -> str:
    events = sorted(events_by_hyp.get(hypothesis_id, []), key=lambda e: e["created_at"])
    for event in reversed(events):
        if event.get("to_status"):
            return event["to_status"]
    return spec.get("hypothesis", {}).get("status", "draft")


def current_status(
    hypothesis_id: str, spec: dict, ledger_path: Path = _DEFAULT_LEDGER_PATH
) -> str:
    """Public wrapper: the ledger's last to_status for this hypothesis, or
    the spec's own status field if nothing's been recorded yet. The ledger
    is authoritative once any event exists -- the spec's status field is
    only an initial/illustrative value, never updated in place."""
    events = [
        e for e in load_events(ledger_path) if e["hypothesis_id"] == hypothesis_id
    ]
    return _latest_status(hypothesis_id, spec, {hypothesis_id: events})


def search(
    family: str | None = None,
    mechanism_keyword: str | None = None,
    dataset: str | None = None,
    exchange: str | None = None,
    symbol: str | None = None,
    decision_reason: str | None = None,
    status: str | None = None,
    rejected_only: bool = False,
    hypotheses_dir: Path = _DEFAULT_HYPOTHESES_DIR,
    ledger_path: Path = _DEFAULT_LEDGER_PATH,
) -> list[dict]:
    """Search hypotheses+ledger jointly. Returns a list of match summaries."""
    specs = load_hypotheses(hypotheses_dir)
    events = load_events(ledger_path)
    events_by_hyp: dict[str, list[dict]] = {}
    for e in events:
        events_by_hyp.setdefault(e["hypothesis_id"], []).append(e)

    results = []
    for hyp_id, spec in specs.items():
        h = spec.get("hypothesis", {})
        hyp_events = events_by_hyp.get(hyp_id, [])
        decision_reasons_seen = {
            e["decision_reason"] for e in hyp_events if e.get("decision_reason")
        }
        current_status = _latest_status(hyp_id, spec, events_by_hyp)

        if family and h.get("family") != family:
            continue
        if mechanism_keyword:
            haystack = " ".join(
                [
                    h.get("title", ""),
                    h.get("summary", ""),
                    h.get("expected_edge_mechanism", ""),
                ]
            ).lower()
            if mechanism_keyword.lower() not in haystack:
                continue
        if dataset:
            datasets = [
                d.get("dataset")
                for d in h.get("data_requirements", {}).get("datasets", [])
            ]
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


def register_cli(sub: argparse._SubParsersAction) -> None:
    """Add the append/search subcommands to a subparsers action (reused by
    both this module's main() and cli.py's `ledger` command)."""
    p_append = sub.add_parser("append", help="Append a ledger event")
    p_append.add_argument("--hypothesis-id", required=True)
    p_append.add_argument(
        "--event-type",
        required=True,
        choices=[
            "status_transition",
            "scan_attached",
            "human_approval",
            "waiver",
            "correction",
            "threshold_change",
            "taxonomy_change",
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
    p_search.add_argument(
        "--mechanism",
        default=None,
        help="keyword matched against title/summary/mechanism",
    )
    p_search.add_argument("--dataset", default=None)
    p_search.add_argument("--exchange", default=None)
    p_search.add_argument("--symbol", default=None)
    p_search.add_argument("--decision-reason", default=None)
    p_search.add_argument("--status", default=None)
    p_search.add_argument("--rejected-only", action="store_true")
    p_search.set_defaults(func=_cmd_search)


def main() -> int:
    parser = argparse.ArgumentParser(description="Kilimanjaro research ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    register_cli(sub)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
