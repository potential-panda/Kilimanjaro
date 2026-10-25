#!/usr/bin/env python3
"""Build a data inventory from the Hase/Komachi-managed lake."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import yaml

_ROOT = Path(__file__).parent.parent
_DEFAULT_INVENTORY_DIR = _ROOT / "cycle" / "03_inventory"
_LAYERS = ("bronze", "silver", "gold", "feature", "research")
_BRONZE = {"Trade", "OrderBook"}


def default_data_root() -> Path:
    """Ask Hase for the Komachi-configured data root."""
    from hase.bronze import data_root

    return data_root()


def _base(root: Path, layer: str, dataset: str, exchange: str, symbol: str) -> Path:
    return (
        root
        / layer
        / f"dataset={dataset}"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
    )


def _dates(base: Path) -> list[str]:
    return sorted(
        path.name[5:]
        for path in base.rglob("date=*")
        if (path / "data.parquet").is_file()
    )


def _missing_dates(
    existing: list[str], start: str | None, end: str | None
) -> list[dict[str, str]]:
    if not start or not end:
        return []
    held, missing = set(existing), []
    day, last = date.fromisoformat(start), date.fromisoformat(end)
    while day <= last:
        if day.isoformat() not in held:
            missing.append(day)
        day += timedelta(days=1)
    spans: list[dict[str, str]] = []
    for day in missing:
        if spans and date.fromisoformat(spans[-1]["end"]) + timedelta(days=1) == day:
            spans[-1]["end"] = day.isoformat()
        else:
            spans.append({"start": day.isoformat(), "end": day.isoformat()})
    return spans


def _columns(base: Path) -> list[str]:
    files = list(base.rglob("data.parquet"))
    if not files:
        return []
    con = duckdb.connect()
    try:
        return [
            row[0]
            for row in con.sql(
                "DESCRIBE SELECT * FROM read_parquet(?)", params=[str(files[0])]
            ).fetchall()
        ]
    finally:
        con.close()


def _datasets(root: Path) -> list[tuple[str, str]]:
    return [
        (layer, path.name[8:])
        for layer in _LAYERS
        if (root / layer).is_dir()
        for path in sorted((root / layer).glob("dataset=*"))
    ]


def build_inventory(
    markets: list[dict],
    start_date: str | None = None,
    end_date: str | None = None,
    data_root: Path | None = None,
) -> dict:
    root = Path(data_root) if data_root else default_data_root()
    entries = []
    for layer, dataset in _datasets(root):
        for market in markets:
            exchange, symbol = market["exchange"], market["symbol"]
            base = _base(root, layer, dataset, exchange, symbol)
            existing, missing = (
                _dates(base),
                _missing_dates(_dates(base), start_date, end_date),
            )
            status = (
                "available"
                if existing and not missing
                else ("partial" if existing else "missing")
            )
            entries.append(
                {
                    "dataset_name": dataset,
                    "catalog_declared_layer": layer,
                    "resolved_layer": layer if base.is_dir() else None,
                    "query_verified": bool(existing),
                    "reliability": "guaranteed" if dataset in _BRONZE else "candidate",
                    "population_action": "none"
                    if status == "available"
                    else (
                        "derive_with_hase"
                        if layer in {"silver", "gold"}
                        else "new_dataset_needed"
                    ),
                    "canonicality": "canonical"
                    if layer in {"bronze", "silver", "gold", "feature"}
                    else "research-ephemeral",
                    "path_template": str(base / "date={date}" / "data.parquet"),
                    "columns": {
                        "metadata_source": "parquet_schema",
                        "names": _columns(base),
                    },
                    "availability": {
                        "status": status,
                        "method": "hase_filesystem",
                        "exchange": exchange,
                        "symbol": symbol,
                        "start_date": start_date,
                        "end_date": end_date,
                        "existing_dates": existing,
                        "missing_dates": missing,
                    },
                    "metadata_warnings": [],
                }
            )
    return {
        "schema_version": 2,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hase": {"data_root": str(root)},
        "datasets": entries,
    }


def write_inventory(
    inventory: dict, inventory_dir: Path = _DEFAULT_INVENTORY_DIR
) -> Path:
    path = (
        inventory_dir
        / f"inventory-{inventory['created_at'].replace(':', '').replace('-', '')}.yaml"
    )
    inventory_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(inventory, sort_keys=False))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Hase data inventory snapshot")
    parser.add_argument(
        "--market", action="append", required=True, metavar="EXCHANGE:SYMBOL"
    )
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--data-root")
    parser.add_argument("--out-dir", default=str(_DEFAULT_INVENTORY_DIR))
    args = parser.parse_args()
    markets = [
        {"exchange": market.split(":", 1)[0], "symbol": market.split(":", 1)[1]}
        for market in args.market
    ]
    inventory = build_inventory(
        markets, args.start, args.end, Path(args.data_root) if args.data_root else None
    )
    print(f"Wrote {write_inventory(inventory, Path(args.out_dir))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
