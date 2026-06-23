#!/usr/bin/env python3
"""Generate a manual Himalaya/potential-panda-core backtest handoff packet
(task 10): the last step of the v1 loop, per
requirements/agent-loop-design.md's "Backtest Handoff Packet" and
handoffs/README.md's naming convention.

A handoff may only be generated once a hypothesis has reached
`backtest-approved` -- a status that only ever comes from a separate,
explicit `human_approval` ledger event (e.g. `tools/ledger.py append
--event-type human_approval --to-status backtest-approved ...`); this
module never writes that event itself, only reads the ledger to confirm it
already happened. It is a task description for a human implementer (entry/
exit sketch, parameter grid, split windows, execution assumptions, expected
outputs, links back to the hypothesis/scan) -- it never generates strategy
code.

Two files are written, matching handoffs/README.md: `hyp-....yaml` (source
of truth) and a generated `hyp-....md` view -- the Markdown is rendered
from the YAML and is never hand-edited.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from ledger import current_status, load_events, load_specs  # noqa: E402
from makalu_client import execution_size_for  # noqa: E402

_ROOT = Path(__file__).parent.parent
_DEFAULT_SPECS_DIR = _ROOT / "specs"
_DEFAULT_LEDGER_PATH = _ROOT / "ledger" / "events.jsonl"
_DEFAULT_SCAN_REPORTS_DIR = _ROOT / "scan-reports"
_DEFAULT_HANDOFFS_DIR = _ROOT / "handoffs"

_EXPECTED_OUTPUTS = [
    "net_pnl_after_costs", "trade_count", "turnover", "win_rate",
    "sharpe_or_equivalent", "max_drawdown", "capacity_sensitivity",
    "parameter_surface", "regime_breakdown",
]


class HandoffError(Exception):
    pass


def _latest_scan_report(hypothesis_id: str, events: list[dict], scan_reports_dir: Path) -> tuple[str | None, dict | None]:
    """Same pattern as tools/results_summary.py: most recent ledger event
    for this hypothesis carrying a scan_report_id, and the report itself if
    its file is still on disk."""
    for event in sorted(events, key=lambda e: e["created_at"], reverse=True):
        rid = (event.get("artifact_ids") or {}).get("scan_report_id")
        if rid:
            path = scan_reports_dir / f"{rid}.json"
            return rid, (json.loads(path.read_text()) if path.is_file() else None)
    return None, None


def _split_windows(report: dict | None) -> tuple[dict, dict, dict]:
    """60/20/20 train/test/forward split of the scanned date window --
    a starting point for the implementer, not a claim that this exact
    split is correct for every hypothesis. Empty dicts when there's no
    scan report yet, or the window is too short to split meaningfully."""
    if report is None:
        return {}, {}, {}
    dw = report["data_window"]
    start, end = date.fromisoformat(dw["start_date"]), date.fromisoformat(dw["end_date"])
    total_days = (end - start).days + 1
    if total_days < 3:
        return {"start_date": str(start), "end_date": str(end)}, {}, {}

    train_days = max(int(total_days * 0.6), 1)
    test_days = max(int(total_days * 0.2), 1)
    train_end = start + timedelta(days=train_days - 1)
    test_start = train_end + timedelta(days=1)
    test_end = min(test_start + timedelta(days=test_days - 1), end)
    forward_start = test_end + timedelta(days=1)

    train_window = {"start_date": str(start), "end_date": str(train_end)}
    test_window = {"start_date": str(test_start), "end_date": str(test_end)} if test_start <= end else {}
    forward_window = {"start_date": str(forward_start), "end_date": str(end)} if forward_start <= end else {}
    return train_window, test_window, forward_window


def _entry_exit_sketch(h: dict, report: dict | None) -> str:
    """Descriptive task text, not executable code -- direction is taken
    from the scan's measured IC sign when available, since a feature
    expression's sign alone doesn't say which way the edge actually runs."""
    feature_expr = h["feature"]["sql_expression"]
    horizon = h["target"]["primary_horizon_seconds"]
    ic = ((report or {}).get("metrics") or {}).get("ic_pooled")
    if ic is not None and ic < 0:
        direction = "short when the signal is positive, long when negative (measured IC is negative)"
    else:
        direction = "long when the signal is positive, short when negative"
    return (
        f"Signal: `{feature_expr}`. Go {direction}. Exit at the primary horizon "
        f"({horizon}s) or per whatever exit rule the implementer chooses in "
        "Himalaya/potential-panda-core -- this is a task description, not executable "
        "strategy code."
    )


def create_handoff(
    hypothesis_id: str,
    specs_dir: Path = _DEFAULT_SPECS_DIR,
    ledger_path: Path = _DEFAULT_LEDGER_PATH,
    scan_reports_dir: Path = _DEFAULT_SCAN_REPORTS_DIR,
) -> dict:
    specs = load_specs(specs_dir)
    spec = specs.get(hypothesis_id)
    if spec is None:
        raise HandoffError(f"no spec found for {hypothesis_id}")

    events = [e for e in load_events(ledger_path) if e["hypothesis_id"] == hypothesis_id]
    status = current_status(hypothesis_id, spec, ledger_path)
    if status != "backtest-approved":
        raise HandoffError(
            f"{hypothesis_id} is '{status}', not 'backtest-approved' -- a handoff can only be "
            "generated after a human approves the scan-passed -> backtest-approved transition"
        )

    approval_events = [
        e for e in events if e.get("event_type") == "human_approval" and e.get("to_status") == "backtest-approved"
    ]
    if not approval_events:
        raise HandoffError(
            f"{hypothesis_id} has status 'backtest-approved' but no human_approval ledger event "
            "recording it -- refusing to generate a handoff without that record"
        )

    h = spec["hypothesis"]
    dr = h.get("data_requirements") or {}
    ea = h.get("execution_assumptions") or {}
    bhi = h.get("backtest_handoff_intent") or {}
    target_market = h["target"]["source_dataset"]

    scan_report_id, report = _latest_scan_report(hypothesis_id, events, scan_reports_dir)
    train_window, test_window, forward_window = _split_windows(report)

    handoff = {
        "handoff": {
            "hypothesis_id": hypothesis_id,
            "scan_report_id": scan_report_id,
            "ledger_approval_event_id": approval_events[-1]["event_id"],
            "intended_strategy_family": h.get("family_label") or h.get("family"),
            "entry_exit_sketch": _entry_exit_sketch(h, report),
            "potential_panda_core_mapping": {
                "base_contract": "qate.trading.strategy",
                "example_variant_reference": bhi.get("potential_panda_core_strategy_pattern") or "",
                "open_questions": [],
            },
            "himalaya_run_config": {
                "markets": [{"exchange": m["exchange"], "symbol": m["symbol"]} for m in dr.get("markets", [])],
                "train_window": train_window,
                "test_window": test_window,
                "forward_window": forward_window,
                "parameter_grid": (h.get("scan_plan") or {}).get("threshold_grid") or [],
                "execution_model": {
                    "fees_bps": ea.get("fees_bps"),
                    "slippage_bps": ea.get("slippage_bps"),
                    "latency_ms": ea.get("latency_ms"),
                    "execution_size": execution_size_for(target_market["symbol"]),
                },
                "himalaya_config_notes": bhi.get("himalaya_config_notes") or "",
            },
            "expected_outputs": list(_EXPECTED_OUTPUTS),
        }
    }
    return handoff


def _render_markdown(handoff: dict) -> str:
    d = handoff["handoff"]
    rc = d["himalaya_run_config"]
    markets_str = ", ".join(f"{m['exchange']}/{m['symbol']}" for m in rc["markets"]) or "(none declared)"
    lines = [
        f"# Backtest Handoff: {d['hypothesis_id']}",
        "",
        f"_Generated from `{d['hypothesis_id']}.yaml` -- do not hand-edit this file._",
        "",
        f"- Scan report: `{d['scan_report_id']}`",
        f"- Ledger approval event: `{d['ledger_approval_event_id']}`",
        f"- Strategy family: {d['intended_strategy_family']}",
        "",
        "## Entry/Exit Sketch",
        "",
        d["entry_exit_sketch"],
        "",
        "## potential-panda-core Mapping",
        "",
        f"- Base contract: `{d['potential_panda_core_mapping']['base_contract']}`",
        f"- Example variant reference: {d['potential_panda_core_mapping']['example_variant_reference'] or '(none yet)'}",
        "",
        "## Himalaya Run Config",
        "",
        f"- Markets: {markets_str}",
        f"- Train window: {rc['train_window'] or '(not enough scanned history to split)'}",
        f"- Test window: {rc['test_window'] or '(not enough scanned history to split)'}",
        f"- Forward window: {rc['forward_window'] or '(not enough scanned history to split)'}",
        f"- Parameter grid: {rc['parameter_grid']}",
        f"- Execution model: {rc['execution_model']}",
        "",
        "## Expected Outputs",
        "",
        *[f"- {o}" for o in d["expected_outputs"]],
        "",
        "_This is a task description for a human implementer -- it does not contain or generate strategy code._",
        "",
    ]
    return "\n".join(lines)


def write_handoff(handoff: dict, handoffs_dir: Path = _DEFAULT_HANDOFFS_DIR) -> tuple[Path, Path]:
    hypothesis_id = handoff["handoff"]["hypothesis_id"]
    handoffs_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = handoffs_dir / f"{hypothesis_id}.yaml"
    md_path = handoffs_dir / f"{hypothesis_id}.md"
    yaml_path.write_text(yaml.dump(handoff, sort_keys=False))
    md_path.write_text(_render_markdown(handoff))
    return yaml_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a backtest handoff packet for a backtest-approved hypothesis")
    parser.add_argument("hypothesis_id")
    parser.add_argument("--specs-dir", default=str(_DEFAULT_SPECS_DIR))
    parser.add_argument("--ledger-path", default=str(_DEFAULT_LEDGER_PATH))
    parser.add_argument("--scan-reports-dir", default=str(_DEFAULT_SCAN_REPORTS_DIR))
    parser.add_argument("--out-dir", default=str(_DEFAULT_HANDOFFS_DIR))
    args = parser.parse_args()

    try:
        handoff = create_handoff(
            args.hypothesis_id, Path(args.specs_dir), Path(args.ledger_path), Path(args.scan_reports_dir)
        )
    except HandoffError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    yaml_path, md_path = write_handoff(handoff, Path(args.out_dir))
    print(f"Wrote {yaml_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
