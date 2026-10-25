#!/usr/bin/env python3
"""Validate a hypothesis spec's data requirements against a live Hase
inventory (task 5): the third layer of spec checking, after schema (task 2,
tools/validate_spec.py) and SQL safety (task 6, tools/sql_safety.py). Those
two only ever say valid/invalid; this one decides which of the three
data-readiness lifecycle statuses a *valid* spec is actually in --
`needs-more-data`, `needs-tooling`, or `scan-ready` -- per
requirements/hypothesis-contract.md's lifecycle and Validation Rules
("data requirements cannot be matched to Makalu inventory").

Two checks here are intentionally NOT live-inventory-dependent, decided
from the spec's own fields instead, because they're about lifecycle status
rather than what Makalu currently has:
  - tools/scanner.py v1 only supports bronze OrderBook as a feature/target
    source dataset, and target.type next_return/spread_adjusted_return --
    anything else can never become scan-ready regardless of what data
    exists, so it's unsupported-feature-transform unconditionally.
  - data_requirements.research_metadata.metadata_status != "complete" maps
    to needs-tooling per the contract's research-label section -- read
    directly off the spec, not derived from inventory.

Everything else here is live: for every (dataset, exchange, symbol) the
spec actually needs (feature/target source datasets, plus the
data_requirements.markets x .datasets cross product), checks the catalog
entry exists, whether Makalu's query engine has it registered, how many
distinct days are actually available, and -- where column metadata is
available -- whether every declared required_columns entry is present.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import inventory as _inventory

_SUPPORTED_TARGET_TYPES = {"next_return", "spread_adjusted_return"}
_SUPPORTED_DATASET = "OrderBook"  # v1 scanner.py only reads bronze OrderBook
# Precomputed feature datasets accepted as a feature source (never as a
# target). Populated into Makalu's feature layer, so availability is checked
# against the Makalu inventory like any other dataset -- no local tree.
_PRECOMPUTED_FEATURE_DATASETS = {"ChartRangeSAR"}


def _unsupported_transform_reasons(h: dict) -> list[str]:
    """Gaps that block scan-ready regardless of data availability -- the
    scanner itself can't run this spec, not just "not yet"."""
    reasons = []
    target_type = h["target"]["type"]
    if target_type not in _SUPPORTED_TARGET_TYPES:
        reasons.append(
            f"target.type '{target_type}' is not implemented by tools/scanner.py v1 "
            f"(only {sorted(_SUPPORTED_TARGET_TYPES)})"
        )

    for ds in h["feature"]["source_datasets"]:
        if (
            ds["dataset"] != _SUPPORTED_DATASET
            and ds["dataset"] not in _PRECOMPUTED_FEATURE_DATASETS
        ):
            reasons.append(
                f"{ds['exchange']}/{ds['symbol']}/{ds['dataset']}: tools/scanner.py v1 only supports bronze "
                f"{_SUPPORTED_DATASET} or a known precomputed feature dataset {sorted(_PRECOMPUTED_FEATURE_DATASETS)} "
                "as a feature source dataset"
            )

    target_ds = h["target"]["source_dataset"]
    if target_ds["dataset"] != _SUPPORTED_DATASET:
        reasons.append(
            f"{target_ds['exchange']}/{target_ds['symbol']}/{target_ds['dataset']}: tools/scanner.py v1 only "
            f"supports bronze {_SUPPORTED_DATASET} as the target source dataset"
        )

    research_metadata = (h.get("data_requirements") or {}).get(
        "research_metadata"
    ) or {}
    if (
        target_type == "research_label"
        and research_metadata.get("metadata_status") != "complete"
    ):
        reasons.append(
            f"research_metadata.metadata_status is {research_metadata.get('metadata_status')!r}, "
            "not 'complete' -- missing build_id/generated_at blocks scan-ready per "
            "hypothesis-contract.md's research-label section"
        )
    return reasons


def _dataset_market_pairs(h: dict) -> list[tuple[str, str, str]]:
    """(dataset, exchange, symbol) tuples the spec actually needs checked:
    the literal feature/target source datasets, plus data_requirements'
    declared markets x datasets cross product (covers any extra context
    dataset listed without being a direct feature/target source, e.g. a
    regime dataset)."""
    pairs = {
        (ds["dataset"], ds["exchange"], ds["symbol"])
        for ds in h["feature"]["source_datasets"]
    }
    t = h["target"]["source_dataset"]
    pairs.add((t["dataset"], t["exchange"], t["symbol"]))
    dr = h.get("data_requirements") or {}
    for ds in dr.get("datasets", []):
        for m in dr.get("markets", []):
            pairs.add((ds["dataset"], m["exchange"], m["symbol"]))
    return sorted(pairs)


def _distinct_days_from_probe(probe: dict) -> int | None:
    """Best-effort distinct-day count from data-check's parsed output --
    None when min/max coverage isn't known at all (e.g. status unknown)."""
    min_date, max_date = probe.get("min_date"), probe.get("max_date")
    if not min_date or not max_date:
        return None
    total = (date.fromisoformat(max_date) - date.fromisoformat(min_date)).days + 1
    return max(total - (probe.get("missing_count") or 0), 0)


def _filesystem_distinct_days(
    dataset_name: str, layer: str, exchange: str, symbol: str, data_root: Path
) -> int:
    base = (
        data_root
        / layer
        / f"dataset={dataset_name}"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
    )
    if not base.is_dir():
        return 0
    return len([p for p in base.glob("date=*") if p.is_dir()])


def assess_data_readiness(
    spec: dict,
    data_root: Path | None = None,
) -> dict:
    """Returns {"status", "decision_reason", "needs_tooling_reasons",
    "needs_more_data_reasons", "datasets_checked"}. Assumes the spec
    already passed tools/validate_spec.py -- this only adds the inventory-
    dependent layer on top."""
    h = spec["hypothesis"]
    needs_tooling_reasons = _unsupported_transform_reasons(h)
    needs_more_data_reasons: list[str] = []

    inventory = _inventory.build_inventory(
        [
            {"exchange": exchange, "symbol": symbol}
            for _, exchange, symbol in _dataset_market_pairs(h)
        ],
        data_root=data_root,
    )
    available = {
        (
            entry["dataset_name"],
            entry["availability"]["exchange"],
            entry["availability"]["symbol"],
        ): entry
        for entry in inventory["datasets"]
    }
    dr = h.get("data_requirements") or {}
    minimum_distinct_days = dr.get("minimum_distinct_days", 1)

    datasets_checked = []
    for dataset_name, exchange, symbol in _dataset_market_pairs(h):
        entry = available.get((dataset_name, exchange, symbol))
        if entry is None:
            needs_tooling_reasons.append(
                f"{exchange}/{symbol}/{dataset_name}: no Hase-managed dataset exists"
            )
            datasets_checked.append(
                {
                    "dataset": dataset_name,
                    "exchange": exchange,
                    "symbol": symbol,
                    "in_catalog": False,
                    "distinct_days": None,
                    "columns": None,
                }
            )
            continue

        resolved_layer = entry["resolved_layer"]
        columns = entry["columns"]["names"]
        existing = entry["availability"]["existing_dates"]
        if resolved_layer is None:
            distinct_days = 0
            needs_more_data_reasons.append(
                f"{exchange}/{symbol}/{dataset_name}: no populated data found in any layer"
            )
        else:
            distinct_days = len(existing)

            if distinct_days is not None and distinct_days < minimum_distinct_days:
                needs_more_data_reasons.append(
                    f"{exchange}/{symbol}/{dataset_name}: only {distinct_days} distinct days available, "
                    f"need at least {minimum_distinct_days}"
                )

        datasets_checked.append(
            {
                "dataset": dataset_name,
                "exchange": exchange,
                "symbol": symbol,
                "in_catalog": True,
                "query_verified": entry["query_verified"],
                "resolved_layer": resolved_layer,
                "distinct_days": distinct_days,
                "columns": columns,
            }
        )

    required_columns = dr.get("required_columns") or []
    known_columns = {
        c
        for entry in datasets_checked
        if entry.get("columns")
        for c in entry["columns"]
    }
    any_columns_known = any(entry.get("columns") for entry in datasets_checked)
    if any_columns_known:
        for col in required_columns:
            if col not in known_columns:
                needs_tooling_reasons.append(
                    f"required_columns column '{col}' not found in any verified dataset's columns"
                )

    if needs_tooling_reasons:
        status, decision_reason = "needs-tooling", "unsupported-feature-transform"
    elif needs_more_data_reasons:
        status, decision_reason = "needs-more-data", "insufficient-data"
    else:
        status, decision_reason = "scan-ready", None

    return {
        "status": status,
        "decision_reason": decision_reason,
        "needs_tooling_reasons": needs_tooling_reasons,
        "needs_more_data_reasons": needs_more_data_reasons,
        "datasets_checked": datasets_checked,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check a hypothesis spec's data requirements against a live Makalu inventory"
    )
    parser.add_argument("spec_path")
    parser.add_argument("--data-root")
    args = parser.parse_args()

    spec = yaml.safe_load(Path(args.spec_path).read_text())
    result = assess_data_readiness(
        spec, Path(args.data_root) if args.data_root else None
    )
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "scan-ready" else 1


if __name__ == "__main__":
    sys.exit(main())
