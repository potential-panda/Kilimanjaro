"""Canonical identities. Paths locate inputs; content/version hashes identify them."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.metadata
import importlib.util
import json
import math
from datetime import date, datetime
from pathlib import Path


def normalize(value):
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("Identity keys must be strings")
        return {k: normalize(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Nonfinite identity value")
        return int(value) if value.is_integer() else value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise TypeError(f"Unsupported identity value: {type(value).__name__}")


def canonical(value) -> str:
    return json.dumps(
        normalize(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def identity(kind: str, value) -> str:
    return f"{kind}-" + hashlib.sha256(canonical(value).encode()).hexdigest()[:32]


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def implementation() -> dict:
    """Hash dirty source too; a git revision alone cannot identify an experiment."""
    src = Path(__file__).resolve().parents[2]
    files = {str(p.relative_to(src)): checksum(p) for p in sorted(src.rglob("*.py"))}
    for path in sorted((src.parent / "cycle" / "01_schemas").glob("research_*.json")):
        files[f"schemas/{path.name}"] = checksum(path)
    versions = {}
    for name in (
        "numpy",
        "pandas",
        "pyarrow",
        "duckdb",
        "scipy",
        "kamakuraquantlab-hase",
        "kamakuraquantlab-komachi",
        "makalu",
        "kamakuraquantlab-qate",
    ):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "unavailable"
    # Editable providers can change without a package-version change.
    for name in ("hase", "komachi", "makalu", "pipeline"):
        spec = importlib.util.find_spec(name)
        if spec and spec.submodule_search_locations:
            for root_str in spec.submodule_search_locations:
                root = Path(root_str)
                for p in sorted(root.rglob("*.py")):
                    files[f"provider/{name}/{p.relative_to(root)}"] = checksum(p)
    return {"source_hash": identity("code", files), "packages": versions}
