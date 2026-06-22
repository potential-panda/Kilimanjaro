#!/usr/bin/env python3
"""Triage summary across every hypothesis: how did $mechanism perform on
$market over $date_range, is it worth digging into further or abandoning,
and has more bronze data become available since it was last scanned.

The hypothesis ID (hyp-YYYYMMDD-NNN-vNN) is just a bookkeeping handle --
this is the actual unit of knowledge the research loop produces. Joins
specs/ + ledger/ + scan-reports/ (each already authoritative for its own
piece) plus a live bronze coverage check, rather than introducing any new
schema or ledger event type. Triage is a presentation-layer interpretation
computed fresh each call, not persisted -- the ledger's decision_reason
stays the source of truth for *why* something failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from ledger import _latest_status, load_events, load_specs  # noqa: E402
from makalu_client import _distinct_dates, bronze_glob  # noqa: E402

_ROOT = Path(__file__).parent.parent
_DEFAULT_SPECS_DIR = _ROOT / "specs"
_DEFAULT_LEDGER_PATH = _ROOT / "ledger" / "events.jsonl"
_DEFAULT_SCAN_REPORTS_DIR = _ROOT / "scan-reports"
_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")

# A "real, statistically-clean signal that's just short on economics" vs.
# "the gap is too large to realistically close" -- found by checking this
# threshold against real cases from this project's own history:
# hyp-20260618-001-v03 (GMO BTC_SPOT, ratio 0.80) and hyp-20260619-002's
# full-history result (ratio 0.66) both felt like genuine "worth trying a
# different market/regime/horizon" cases when reasoned through by hand;
# hyp-20260618-001 (GMO BTC_JPY, ratio 0.25) did not. Tunable -- not a
# claim that 0.5 is precisely the right cutoff, just where this project's
# own judgment calls happened to land.
_CLOSE_RATIO_THRESHOLD = 0.5

_WEAK_SIGNAL_REASONS = {"failed-ic-threshold", "failed-hit-rate-threshold"}
_NEEDS_DATA_STATUSES = {"needs-more-data", "needs-tooling"}
_PASS_STATUSES = {"pass", "backtest-approved"}


def _events_by_hypothesis(ledger_path: Path) -> dict[str, list[dict]]:
    by_hyp: dict[str, list[dict]] = {}
    for event in load_events(ledger_path):
        by_hyp.setdefault(event["hypothesis_id"], []).append(event)
    for events in by_hyp.values():
        events.sort(key=lambda e: e["created_at"])
    return by_hyp


def classify_triage(verdict_status: str, failed_rules: list[str], cost_buffer: dict | None) -> str:
    """Pure function over an already-computed verdict -- see module
    docstring and _CLOSE_RATIO_THRESHOLD for the reasoning."""
    if verdict_status in _PASS_STATUSES:
        return "pass"
    if verdict_status in _NEEDS_DATA_STATUSES:
        return "needs_data"
    if verdict_status != "fail":
        return "review"

    if _WEAK_SIGNAL_REASONS & set(failed_rules):
        return "weak"

    if failed_rules == ["failed-cost-buffer"] and cost_buffer:
        ratio = cost_buffer["edge_bps"] / cost_buffer["required_bps"] if cost_buffer["required_bps"] else 0
        return "close" if ratio >= _CLOSE_RATIO_THRESHOLD else "weak"

    return "review"


def _bronze_date_range(exchange: str, symbol: str, data_root: Path) -> tuple[str | None, str | None]:
    glob = bronze_glob(exchange, symbol)
    con = duckdb.connect()
    try:
        df = con.sql(f"SELECT min(date) AS lo, max(date) AS hi FROM read_parquet('{glob}', hive_partitioning=true)").df()
    except duckdb.IOException:
        return None, None
    row = df.iloc[0]
    if pd.isna(row["lo"]):
        return None, None
    return str(row["lo"].date()), str(row["hi"].date())


def _more_data_available_days(exchange: str, symbol: str, scanned_start: str | None, scanned_end: str | None, bronze_min: str | None, bronze_max: str | None) -> int:
    """Count of bronze days outside the already-scanned window -- not just
    a date-range comparison, since real gaps (see makalu_client.py) mean
    a wider date range doesn't always mean proportionally more days."""
    if bronze_min is None or scanned_start is None:
        return 0
    glob = bronze_glob(exchange, symbol)
    bronze_days = _distinct_dates(glob, bronze_min, bronze_max)
    scanned_days = set(pd.date_range(scanned_start, scanned_end).date)
    return len(bronze_days - scanned_days)


def build_results(
    specs_dir: Path = _DEFAULT_SPECS_DIR,
    ledger_path: Path = _DEFAULT_LEDGER_PATH,
    scan_reports_dir: Path = _DEFAULT_SCAN_REPORTS_DIR,
    data_root: Path = _DEFAULT_DATA_ROOT,
    check_bronze: bool = True,
) -> list[dict]:
    specs = load_specs(specs_dir)
    events_by_hyp = _events_by_hypothesis(ledger_path)

    rows = []
    for hyp_id, spec in specs.items():
        h = spec["hypothesis"]
        target = h["target"]["source_dataset"]
        events = events_by_hyp.get(hyp_id, [])
        status = _latest_status(hyp_id, spec, {hyp_id: events})

        latest_scan_id = None
        report = None
        for event in reversed(events):
            rid = event.get("artifact_ids", {}).get("scan_report_id")
            if rid:
                latest_scan_id = rid
                path = scan_reports_dir / f"{rid}.json"
                if path.is_file():
                    report = json.loads(path.read_text())
                break

        decision_reasons = sorted({e["decision_reason"] for e in events if e.get("decision_reason")})

        row = {
            "hyp_id": hyp_id,
            "family": h.get("family"),
            "target_exchange": target.get("exchange"),
            "target_symbol": target.get("symbol"),
            "latest_scan_id": latest_scan_id,
            "scanned_start_date": None,
            "scanned_end_date": None,
            "scanned_distinct_days": None,
            "status": status,
            "decision_reasons": decision_reasons,
            "ic_pooled": None,
            "hit_rate": None,
            "edge_bps": None,
            "required_bps": None,
            "cost_buffer_ratio": None,
            "triage": "untested" if report is None else None,
            "bronze_min_date": None,
            "bronze_max_date": None,
            "more_data_available_days": 0,
        }

        if report is not None:
            dw = report["data_window"]
            row["scanned_start_date"] = dw["start_date"]
            row["scanned_end_date"] = dw["end_date"]
            row["scanned_distinct_days"] = dw["distinct_days"]
            row["ic_pooled"] = report["metrics"]["ic_pooled"]
            row["hit_rate"] = report["metrics"]["hit_rate"]

            tv = report["threshold_verdict"]
            cb = tv.get("cost_buffer")
            if cb:
                row["edge_bps"] = cb["edge_bps"]
                row["required_bps"] = cb["required_bps"]
                row["cost_buffer_ratio"] = cb["edge_bps"] / cb["required_bps"] if cb["required_bps"] else None
            row["triage"] = classify_triage(tv["status"], tv.get("failed_rules", []), cb)

        if check_bronze:
            bronze_min, bronze_max = _bronze_date_range(target["exchange"], target["symbol"], data_root)
            row["bronze_min_date"] = bronze_min
            row["bronze_max_date"] = bronze_max
            if report is not None:
                row["more_data_available_days"] = _more_data_available_days(
                    target["exchange"], target["symbol"],
                    row["scanned_start_date"], row["scanned_end_date"], bronze_min, bronze_max,
                )

        rows.append(row)

    rows.sort(key=lambda r: (r["family"] or "", r["hyp_id"]))
    return rows


def _print_table(rows: list[dict]) -> None:
    headers = ["hyp_id", "family", "market", "scanned", "triage", "ratio", "+data"]
    widths = [22, 24, 18, 22, 10, 6, 6]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    for r in rows:
        market = f"{r['target_exchange']}/{r['target_symbol']}"
        scanned = f"{r['scanned_start_date']}..{r['scanned_end_date']} ({r['scanned_distinct_days']}d)" if r["scanned_start_date"] else "-"
        ratio = f"{r['cost_buffer_ratio']:.2f}" if r["cost_buffer_ratio"] is not None else "-"
        more = f"+{r['more_data_available_days']}d" if r["more_data_available_days"] else ""
        print("  ".join(str(v).ljust(w) for v, w in zip(
            [r["hyp_id"], r["family"], market, scanned, r["triage"], ratio, more], widths
        )))


def main() -> int:
    parser = argparse.ArgumentParser(description="Triage summary across every hypothesis")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    parser.add_argument("--no-bronze-check", action="store_true", help="skip the live bronze coverage check (faster)")
    args = parser.parse_args()

    rows = build_results(check_bronze=not args.no_bronze_check)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        _print_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
