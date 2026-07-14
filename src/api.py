#!/usr/bin/env python3
"""Kilimanjaro research-loop HTTP API -- port 9777.

Read-only visualization of results/grid_scan/, specs/, and ledger/ -- the
same file layout convention as Makalu's src/api.py (web/ for the frontend,
served as static files from this same process). Nothing here writes to any
of those directories; this is a viewer, not a control surface -- mutating
the research loop stays in tools/*.py and the CLI, on purpose.

Endpoints
---------
GET /api/scans                          all grid-scan runs, newest first
GET /api/scans/<kind>/<scan_id>         one run: params.json + file list
GET /api/scans/<kind>/<scan_id>/file/<name>
                                        a CSV from the run folder as
                                        {columns, rows} (client sorts)
GET /api/hypotheses                     spec+ledger join, one row per hyp
GET /api/hypotheses/<hyp_id>            full spec + event history
GET /api/ledger                         raw ledger events
"""

import csv
import json
import sys
from pathlib import Path

from flask import Flask, abort, jsonify, send_from_directory

_ROOT = Path(__file__).parent.parent
_SPECS_DIR = _ROOT / "specs"
_LEDGER_PATH = _ROOT / "ledger" / "events.jsonl"
_WEB_DIR = _ROOT / "web"
_GRID_SCAN_DIR = _ROOT / "results" / "grid_scan"
_SCAN_KINDS = ("flip", "horizon", "expr")

sys.path.insert(0, str(_ROOT / "tools"))
from ledger import _latest_status, load_events, load_specs  # noqa: E402

app = Flask(__name__, static_folder=str(_WEB_DIR))

# scan-list cache: scan folder path -> (mtime, summary dict)
_scan_cache: dict[str, tuple[float, dict]] = {}


# -- Scans ----------------------------------------------------------------


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with open(path, newline="") as f:
        reader = csv.reader(f)
        try:
            columns = next(reader)
        except StopIteration:
            return [], []
        return columns, [row for row in reader]


def _scan_summary(kind: str, folder: Path) -> dict:
    """Cheap summary for the list view, cached by output.csv mtime."""
    out_csv = folder / "output.csv"
    mtime = out_csv.stat().st_mtime if out_csv.is_file() else folder.stat().st_mtime
    cached = _scan_cache.get(str(folder))
    if cached and cached[0] == mtime:
        return cached[1]

    params: dict = {}
    params_file = folder / "params.json"
    if params_file.is_file():
        try:
            params = json.loads(params_file.read_text())
        except json.JSONDecodeError:
            params = {}

    n_combos = None
    n_stable = None
    verdicts: dict[str, int] = {}
    if out_csv.is_file():
        columns, rows = _read_csv(out_csv)
        n_combos = len(rows)
        if "verdict" in columns:
            vi = columns.index("verdict")
            for row in rows:
                v = row[vi] or "?"
                verdicts[v] = verdicts.get(v, 0) + 1
            n_stable = verdicts.get("stable", 0)
        elif "stable" in columns:
            si = columns.index("stable")
            n_stable = sum(1 for row in rows if row[si] == "True")

    summary = {
        "kind": kind,
        "scan_id": folder.name,
        "market": params.get("market"),
        "start": params.get("start"),
        "end": params.get("end"),
        "windows": params.get("windows"),
        "n_combos": n_combos,
        "n_stable": n_stable,
        "verdicts": verdicts,
        "mtime": mtime,
    }
    _scan_cache[str(folder)] = (mtime, summary)
    return summary


def _scan_folder(kind: str, scan_id: str) -> Path:
    if kind not in _SCAN_KINDS or "/" in scan_id or ".." in scan_id:
        abort(404)
    folder = _GRID_SCAN_DIR / kind / scan_id
    if not folder.is_dir():
        abort(404)
    return folder


@app.route("/api/scans")
def api_scans():
    out = []
    for kind in _SCAN_KINDS:
        kind_dir = _GRID_SCAN_DIR / kind
        if not kind_dir.is_dir():
            continue
        for folder in kind_dir.iterdir():
            if folder.is_dir():
                out.append(_scan_summary(kind, folder))
    out.sort(key=lambda s: -s["mtime"])
    return jsonify(out)


@app.route("/api/scans/<kind>/<scan_id>")
def api_scan_detail(kind: str, scan_id: str):
    folder = _scan_folder(kind, scan_id)
    params = {}
    if (folder / "params.json").is_file():
        params = json.loads((folder / "params.json").read_text())
    files = sorted(p.name for p in folder.glob("*.csv"))
    return jsonify({
        "summary": _scan_summary(kind, folder),
        "params": params,
        "files": files,
    })


@app.route("/api/scans/<kind>/<scan_id>/file/<name>")
def api_scan_file(kind: str, scan_id: str, name: str):
    folder = _scan_folder(kind, scan_id)
    if "/" in name or ".." in name or not name.endswith(".csv"):
        abort(404)
    path = folder / name
    if not path.is_file():
        abort(404)
    columns, rows = _read_csv(path)
    return jsonify({"name": name, "columns": columns, "rows": rows})


# -- Hypotheses / ledger ----------------------------------------------------


def _events_by_hypothesis() -> dict[str, list[dict]]:
    by_hyp: dict[str, list[dict]] = {}
    for event in load_events(_LEDGER_PATH):
        by_hyp.setdefault(event["hypothesis_id"], []).append(event)
    for events in by_hyp.values():
        events.sort(key=lambda e: e["created_at"])
    return by_hyp


def _hypothesis_summary(hyp_id: str, spec: dict, events: list[dict]) -> dict:
    h = spec["hypothesis"]
    decision_reasons = sorted(
        {e["decision_reason"] for e in events if e.get("decision_reason")}
    )
    scan_ids: list[str] = []
    for e in events:
        ids = e.get("artifact_ids", {}).get("scan_report_ids") or []
        one = e.get("artifact_ids", {}).get("scan_report_id")
        if one:
            ids = ids + [one]
        scan_ids.extend(i for i in ids if i not in scan_ids)
    return {
        "id": hyp_id,
        "parent_id": h.get("parent_id"),
        "family": h.get("family"),
        "title": h.get("title"),
        "status": _latest_status(hyp_id, spec, {hyp_id: events}),
        "decision_reasons": decision_reasons,
        "event_count": len(events),
        "scan_report_ids": scan_ids,
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


@app.route("/api/ledger")
def api_ledger():
    return jsonify(load_events(_LEDGER_PATH))


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
