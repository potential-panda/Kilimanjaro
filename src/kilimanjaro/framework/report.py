"""Output helpers: CSV writing and scan folder layout."""

from __future__ import annotations

import csv
import json
from pathlib import Path


def write_csv(rows: list[dict], path: Path, fields: list[str]) -> None:
    """Write rows to CSV, silently ignoring any dict keys not in fields."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_scan_folder(
    out_dir: Path,
    params: dict,
    rows: list[dict],
    filtered_rows: list[dict],
    fields: list[str],
    extra_csvs: dict[str, tuple[list[dict], list[str]]] | None = None,
) -> None:
    """Write a complete scan output folder.

    Layout
    ------
    out_dir/
        params.json           scan configuration (reproducibility)
        output.csv            all combos
        output_filtered.csv   combos passing quality gates
        {name}.csv ...        any extra CSVs (e.g. age_stats.csv)

    Parameters
    ----------
    extra_csvs : dict mapping filename → (rows, fields)
        e.g. {"age_stats.csv": (age_stat_rows, AGE_STAT_FIELDS)}
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "params.json").write_text(json.dumps(params, indent=2))
    write_csv(rows, out_dir / "output.csv", fields)
    write_csv(filtered_rows, out_dir / "output_filtered.csv", fields)
    if extra_csvs:
        for fname, (data, flds) in extra_csvs.items():
            write_csv(data, out_dir / fname, flds)
