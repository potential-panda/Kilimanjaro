#!/usr/bin/env python3
"""Build a Kilimanjaro data inventory snapshot against a live Makalu instance.

Implements the Bronze-First Reliability Model from
requirements/makalu-integration-contract.md: bronze (Trade, OrderBook) is
the only dataset trusted without verification; every other layer is
independently checked for actual queryability (not inferred from
catalog.yaml's declared layer, which can disagree with the real on-disk
location -- see "Known Gap" in that doc) and routed to a population_action
when it isn't usable for the requested scope.

v1 scope/limitations (documented, not silently assumed):
  - Availability is checked via `--exchange`/`--symbol` only. Datasets with
    extra required partitions (MarketPrice/VolSpread's execution_size,
    VolSpread/Chart_*/LeadLag's param_id, LeadLag's market_a/market_b) are
    marked query_verified from `query --list`/DESCRIBE as usual, but their
    date-availability check is skipped here (status: "unknown") -- a
    hypothesis that actually needs one of those datasets resolves its own
    specific partition via the SQL DISTINCT recipe in the integration
    contract, not via this general snapshot.
  - No writes occur to Makalu. Running a `populate` command in response to
    a `populate` routing decision is a separate, explicit action this
    module never takes on its own.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

_ROOT = Path(__file__).parent.parent
_DEFAULT_MAKALU_ROOT = Path("/home/lulurun/workspace/Makalu")
_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_DEFAULT_INVENTORY_DIR = _ROOT / "cycle" / "03_inventory"

_ALL_LAYERS = ["bronze", "silver", "gold", "feature", "research"]
_CANONICAL_LAYERS = {"bronze", "silver", "gold", "feature"}

# Confirmed against `python cli.py populate --help` -- see
# makalu-integration-contract.md's "Known populate commands".
_POPULATE_SUBCOMMAND = {
    "Trade": "bronze",
    "OrderBook": "bronze",
    "BookChange": "BookChange",
    "BookState": "BookState",
    "TradeAggression": "TradeAggression",
    "MarketPrice": "MarketPrice",
    "VolSpread": "VolSpread",
    "LeadLag": "LeadLag",
}

_BASE_PARTITIONS = {"exchange", "symbol"}


def needs_extra_partitions(catalog_entry: dict) -> bool:
    """True if this dataset's catalog partition_columns include anything
    beyond exchange/symbol (e.g. MarketPrice/VolSpread's execution_size,
    VolSpread/Chart_*/TripleBarrierLabel*'s param_id, LeadLag's
    market_a/market_b). v1 skips date-availability checks for these --
    confirmed against catalog.yaml (2026-06-18) that this is a real,
    catalog-declared property, not a fixed list to keep in sync by hand."""
    return bool(set(catalog_entry.get("partition_columns", [])) - _BASE_PARTITIONS)


def _run_cli(makalu_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(makalu_root / "src" / "cli.py")] + args,
        capture_output=True,
        text=True,
        timeout=30,
    )


def load_catalog(makalu_root: Path = _DEFAULT_MAKALU_ROOT) -> list[dict]:
    catalog_path = makalu_root / "doc" / "catalog.yaml"
    return yaml.safe_load(catalog_path.read_text()).get("datasets", [])


def query_list(makalu_root: Path = _DEFAULT_MAKALU_ROOT) -> dict[str, dict]:
    """Datasets the query engine has actually registered (have data under
    their catalog-declared layer path). Returns {} on any CLI failure."""
    result = _run_cli(makalu_root, ["query", "--list"])
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return {d["dataset"]: d for d in data.get("datasets", [])}


def describe_view(
    view_name: str, makalu_root: Path = _DEFAULT_MAKALU_ROOT
) -> list[str] | None:
    """Column names via DESCRIBE, or None if the view isn't registered/query fails."""
    result = _run_cli(makalu_root, ["query", "--sql", f"DESCRIBE {view_name}"])
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if "error" in data:
        return None
    return [row["column_name"] for row in data.get("rows", [])]


def resolve_actual_layer(
    dataset_name: str, catalog_layer: str, data_root: Path = _DEFAULT_DATA_ROOT
) -> str | None:
    """Filesystem probe for which layer directory actually has this dataset's
    data on disk, independent of what the catalog claims."""
    if (data_root / catalog_layer / f"dataset={dataset_name}").is_dir():
        return catalog_layer
    for layer in _ALL_LAYERS:
        if (
            layer != catalog_layer
            and (data_root / layer / f"dataset={dataset_name}").is_dir()
        ):
            return layer
    return None


_DATE_RE = r"\d{4}-\d{2}-\d{2}"
_RANGE_RE = re.compile(rf"({_DATE_RE})(?:\s*(?:→|->)\s*({_DATE_RE}))?")


def parse_data_check(output: str) -> dict:
    """Parse data-check's text output. See makalu-integration-contract.md's
    "Availability Probe Contract" for the real (Unicode-arrow) format.

    data-check exits 0 even for an unsupported dataset -- it just logs an
    "Unknown dataset" line to stdout instead of any of the expected labels.
    Must be detected explicitly, not left to fall through to a generic
    "unknown" status for the wrong reason.
    """
    if "Unknown dataset" in output:
        return {"status": "unsupported_by_data_check"}
    if "No data for" in output:
        # e.g. "No data for exchange=GMO under /panda/makalu-data/silver/dataset=BookState"
        # -- an explicit absence signal, not a parse failure; must not fall
        # through to a generic "unknown" for the wrong reason.
        return {"status": "missing", "missing_dates": []}

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if lines and lines[0].startswith("Available "):
        key = lines[0][len("Available ") : -1]
        return {
            "status": "needs_partition_value",
            "missing_partition": key,
            "values": lines[1:],
        }

    result: dict = {}
    missing_dates: list[str] = []
    in_missing_block = False
    for line in lines:
        if line.startswith("Dataset"):
            result["dataset"] = line.split(":", 1)[1].strip()
        elif line.startswith("Location"):
            result["location"] = line.split(":", 1)[1].strip()
            result["resolved_layer"] = _layer_from_location(result["location"])
        elif line.startswith("Min date"):
            result["min_date"] = line.split(":", 1)[1].strip()
        elif line.startswith("Max date"):
            result["max_date"] = line.split(":", 1)[1].strip()
        elif line.startswith("Window"):
            in_missing_block = False
        elif line.startswith("Missing"):
            in_missing_block = True
            m = re.search(r"Missing\s*:\s*(\d+)\s*days", line)
            result["missing_count"] = int(m.group(1)) if m else None
        elif in_missing_block:
            m = _RANGE_RE.match(line)
            if m:
                missing_dates.append(
                    {"start": m.group(1), "end": m.group(2) or m.group(1)}
                )
    result["missing_dates"] = missing_dates
    result["status"] = (
        "available"
        if result.get("missing_count") == 0
        else ("partial" if "missing_count" in result else "unknown")
    )
    return result


def _layer_from_location(location: str) -> str | None:
    m = re.search(r"/makalu-data/([a-z]+)/dataset=", location)
    return m.group(1) if m else None


def data_check(
    dataset: str,
    exchange: str,
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    makalu_root: Path = _DEFAULT_MAKALU_ROOT,
) -> dict:
    args = [
        "data-check",
        "--dataset",
        dataset,
        "--exchange",
        exchange,
        "--symbol",
        symbol,
    ]
    if start_date:
        args += ["--start", start_date]
    if end_date:
        args += ["--end", end_date]
    result = _run_cli(makalu_root, args)
    if result.returncode != 0:
        return {"status": "unknown", "error": result.stderr.strip()}
    return parse_data_check(result.stdout)


def filesystem_date_probe(
    dataset_name: str,
    layer: str,
    exchange: str,
    symbol: str,
    start_date: str | None,
    end_date: str | None,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> dict:
    """Fallback per makalu-integration-contract.md's "Fallback for
    Feature/Research Datasets" -- used whenever data-check doesn't cover a
    dataset (today: feature and research layers)."""
    base = (
        data_root
        / layer
        / f"dataset={dataset_name}"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
    )
    existing = (
        sorted(p.name.split("=", 1)[1] for p in base.glob("date=*") if p.is_dir())
        if base.is_dir()
        else []
    )

    if not start_date or not end_date:
        return {
            "status": "available" if existing else "missing",
            "missing_dates": [],
        }

    from datetime import date, timedelta

    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    existing_set = set(existing)
    missing: list[str] = []
    d = start
    while d <= end:
        if d.isoformat() not in existing_set:
            missing.append(d.isoformat())
        d += timedelta(days=1)

    # Collapse consecutive missing dates into ranges, matching data-check's shape.
    ranges: list[dict] = []
    for d_str in missing:
        if (
            ranges
            and (date.fromisoformat(ranges[-1]["end"]) + timedelta(days=1)).isoformat()
            == d_str
        ):
            ranges[-1]["end"] = d_str
        else:
            ranges.append({"start": d_str, "end": d_str})

    if not missing:
        status = "available"
    elif not existing_set:
        status = (
            "missing"  # no data for this exchange/symbol at all, not just this window
        )
    else:
        status = "partial"
    return {"status": status, "missing_dates": ranges}


def population_action_for(
    dataset_name: str,
    layer: str,
    query_verified: bool,
    has_any_data: bool,
    scope_available: bool,
) -> str:
    if query_verified and scope_available:
        return "none"
    if dataset_name in _POPULATE_SUBCOMMAND:
        return "populate"
    if layer in ("feature", "research", "silver", "gold"):
        return "derive_from_bronze"
    return "new_dataset_needed"


def build_dataset_entry(
    catalog_entry: dict,
    exchange: str,
    symbol: str,
    start_date: str | None,
    end_date: str | None,
    registered: dict[str, dict],
    makalu_root: Path = _DEFAULT_MAKALU_ROOT,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> dict:
    name = catalog_entry["dataset_name"]
    catalog_layer = catalog_entry["layer"]
    view_name = name.lower()

    query_verified = name in registered
    resolved_layer = (
        catalog_layer
        if query_verified
        else resolve_actual_layer(name, catalog_layer, data_root)
    )
    has_any_data = resolved_layer is not None
    # Reliability is about whether this dataset *actually is* bronze, not
    # what the catalog claims -- BookState/BookChange's catalog_layer says
    # "bronze" but that's exactly the mismatch this model exists to not trust.
    reliability = "guaranteed" if name in ("Trade", "OrderBook") else "candidate"
    canonicality = (
        "canonical" if catalog_layer in _CANONICAL_LAYERS else "research-ephemeral"
    )

    columns = describe_view(view_name, makalu_root) if query_verified else None

    availability: dict = {
        "status": "unknown",
        "method": None,
        "exchange": exchange,
        "symbol": symbol,
        "start_date": start_date,
        "end_date": end_date,
        "existing_dates": [],
        "missing_dates": [],
    }
    warnings: list[str] = []

    if needs_extra_partitions(catalog_entry):
        extra = sorted(
            set(catalog_entry.get("partition_columns", [])) - _BASE_PARTITIONS
        )
        warnings.append(
            f"{name} requires extra partitions {extra} beyond exchange/symbol; date-availability check skipped in v1"
        )
    elif resolved_layer is not None:
        probe = data_check(name, exchange, symbol, start_date, end_date, makalu_root)
        if probe.get("status") == "unsupported_by_data_check":
            warnings.append(
                f"data-check does not support {name}; used filesystem fallback per the integration contract"
            )
            probe = filesystem_date_probe(
                name, resolved_layer, exchange, symbol, start_date, end_date, data_root
            )
            availability["method"] = "path_template_filesystem"
        else:
            availability["method"] = "data_check"
        availability["status"] = probe.get("status", "unknown")
        availability["missing_dates"] = probe.get("missing_dates", [])
        if probe.get("status") == "needs_partition_value":
            availability["status"] = "unknown"
            warnings.append(
                f"data-check needs an explicit value: {probe.get('missing_partition')}"
            )
    else:
        availability["status"] = "missing"

    scope_available = availability["status"] == "available"
    population_action = population_action_for(
        name, catalog_layer, query_verified, has_any_data, scope_available
    )

    if catalog_layer != resolved_layer and resolved_layer is not None:
        warnings.append(
            f"catalog_declared_layer ({catalog_layer}) disagrees with resolved_layer ({resolved_layer}); "
            "query engine does not register this view"
        )
    if not has_any_data:
        warnings.append(f"no populated data found for {name} in any layer")

    return {
        "dataset_name": name,
        "view_name": view_name,
        "catalog_declared_layer": catalog_layer,
        "resolved_layer": resolved_layer,
        "query_verified": query_verified,
        "reliability": reliability,
        "population_action": population_action,
        "canonicality": canonicality,
        "primary_timestamp": catalog_entry.get("primary_timestamp"),
        "partition_columns": catalog_entry.get("partition_columns", []),
        "source_dataset": catalog_entry.get("source_dataset"),
        "path_template": f"{data_root}/{resolved_layer or catalog_layer}/dataset={name}/exchange={{exchange}}/symbol={{symbol}}/date={{date}}/data.parquet",
        "columns": {
            "metadata_source": "describe"
            if columns is not None
            else "dataset_definitions_md",
            "names": columns or [],
        },
        "availability": availability,
        "metadata_warnings": warnings,
    }


def build_inventory(
    markets: list[dict],
    start_date: str | None = None,
    end_date: str | None = None,
    makalu_root: Path = _DEFAULT_MAKALU_ROOT,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> dict:
    catalog = load_catalog(makalu_root)
    registered = query_list(makalu_root)
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    datasets = []
    for entry in catalog:
        for market in markets:
            datasets.append(
                build_dataset_entry(
                    entry,
                    market["exchange"],
                    market["symbol"],
                    start_date,
                    end_date,
                    registered,
                    makalu_root,
                    data_root,
                )
            )

    return {
        "schema_version": 1,
        "created_at": created_at,
        "makalu": {
            "catalog_path": str(makalu_root / "doc" / "catalog.yaml"),
            "query_cli": f"python {makalu_root / 'src' / 'cli.py'} query",
        },
        "datasets": datasets,
    }


def write_inventory(
    inventory: dict, inventory_dir: Path = _DEFAULT_INVENTORY_DIR
) -> Path:
    ts = inventory["created_at"].replace(":", "").replace("-", "")
    path = inventory_dir / f"inventory-{ts}.yaml"
    inventory_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(inventory, sort_keys=False))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a Kilimanjaro data inventory snapshot"
    )
    parser.add_argument(
        "--market",
        action="append",
        required=True,
        metavar="EXCHANGE:SYMBOL",
        help="Repeatable, e.g. --market GMO:BTC_JPY --market BINANCE:BTC_USDT",
    )
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--makalu-root", default=str(_DEFAULT_MAKALU_ROOT))
    parser.add_argument("--out-dir", default=str(_DEFAULT_INVENTORY_DIR))
    args = parser.parse_args()

    markets = []
    for m in args.market:
        exchange, symbol = m.split(":", 1)
        markets.append({"exchange": exchange, "symbol": symbol})

    inventory = build_inventory(
        markets,
        start_date=args.start,
        end_date=args.end,
        makalu_root=Path(args.makalu_root),
    )
    path = write_inventory(inventory, Path(args.out_dir))
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
