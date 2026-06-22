#!/usr/bin/env python3
"""Kilimanjaro research-loop HTTP API -- port 9777.

Read-only visualization of specs/, scan-reports/, and ledger/ -- the same
file layout convention as Makalu's src/api.py (web/ for the frontend,
served as static files from this same process). Nothing here writes to
any of those directories; this is a viewer, not a control surface --
mutating the research loop stays in tools/*.py and the CLI, on purpose.
"""

import json
import sys
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

_ROOT = Path(__file__).parent.parent
_SPECS_DIR = _ROOT / "specs"
_SCAN_REPORTS_DIR = _ROOT / "scan-reports"
_LEDGER_PATH = _ROOT / "ledger" / "events.jsonl"
_WEB_DIR = _ROOT / "web"

sys.path.insert(0, str(_ROOT / "tools"))
from ledger import _latest_status, load_events, load_specs  # noqa: E402
from results_summary import build_results  # noqa: E402

app = Flask(__name__, static_folder=str(_WEB_DIR))


def _events_by_hypothesis() -> dict[str, list[dict]]:
    by_hyp: dict[str, list[dict]] = {}
    for event in load_events(_LEDGER_PATH):
        by_hyp.setdefault(event["hypothesis_id"], []).append(event)
    for events in by_hyp.values():
        events.sort(key=lambda e: e["created_at"])
    return by_hyp


def _hypothesis_summary(hyp_id: str, spec: dict, events: list[dict]) -> dict:
    h = spec["hypothesis"]
    decision_reasons = sorted({e["decision_reason"] for e in events if e.get("decision_reason")})
    scan_report_ids = [
        e["artifact_ids"]["scan_report_id"]
        for e in events
        if e.get("artifact_ids", {}).get("scan_report_id")
    ]
    return {
        "id": hyp_id,
        "parent_id": h.get("parent_id"),
        "family": h.get("family"),
        "title": h.get("title"),
        "status": _latest_status(hyp_id, spec, {hyp_id: events}),
        "decision_reasons": decision_reasons,
        "event_count": len(events),
        "scan_report_ids": scan_report_ids,
        "latest_scan_report_id": scan_report_ids[-1] if scan_report_ids else None,
    }


@app.route("/api/hypotheses")
def api_hypotheses():
    specs = load_specs(_SPECS_DIR)
    events_by_hyp = _events_by_hypothesis()
    out = [
        _hypothesis_summary(hyp_id, spec, events_by_hyp.get(hyp_id, []))
        for hyp_id, spec in specs.items()
    ]
    out.sort(key=lambda h: h["id"])
    return jsonify(out)


@app.route("/api/hypotheses/<hyp_id>")
def api_hypothesis_detail(hyp_id: str):
    specs = load_specs(_SPECS_DIR)
    spec = specs.get(hyp_id)
    if spec is None:
        abort(404)
    events = _events_by_hypothesis().get(hyp_id, [])
    return jsonify({
        "summary": _hypothesis_summary(hyp_id, spec, events),
        "spec": spec,
        "events": events,
    })


@app.route("/api/scan-reports/<report_id>")
def api_scan_report(report_id: str):
    path = _SCAN_REPORTS_DIR / f"{report_id}.json"
    if not path.is_file():
        abort(404)
    return jsonify(json.loads(path.read_text()))


@app.route("/api/ledger")
def api_ledger():
    return jsonify(load_events(_LEDGER_PATH))


@app.route("/api/results")
def api_results():
    no_bronze = request.args.get("no_bronze_check") == "1"
    return jsonify(build_results(check_bronze=not no_bronze))


# -- Static / SPA --------------------------------------------------------

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_web(path: str):
    if path.startswith("api/"):
        return jsonify({"error": "not found"}), 404
    fp = _WEB_DIR / path
    if path and fp.is_file():
        return send_from_directory(str(_WEB_DIR), path)
    return send_from_directory(str(_WEB_DIR), "index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9777, debug=False, threaded=True)
