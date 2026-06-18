#!/usr/bin/env python3
"""Load and validate Kilimanjaro's tradeable market registry (markets/registry.yaml)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import yaml

_ROOT = Path(__file__).parent.parent
_DEFAULT_REGISTRY_PATH = _ROOT / "markets" / "registry.yaml"
_SCHEMA_PATH = _ROOT / "schemas" / "market_registry.schema.json"


def load_registry(path: Path = _DEFAULT_REGISTRY_PATH) -> dict:
    registry = yaml.safe_load(path.read_text())
    schema = json.loads(_SCHEMA_PATH.read_text())
    jsonschema.Draft202012Validator(schema).validate(registry)
    return registry


def markets(path: Path = _DEFAULT_REGISTRY_PATH) -> list[dict]:
    return load_registry(path)["markets"]


def find_market(exchange: str, symbol: str, path: Path = _DEFAULT_REGISTRY_PATH) -> dict | None:
    for m in markets(path):
        if m["exchange"] == exchange and m["symbol"] == symbol:
            return m
    return None


def main() -> int:
    reg = load_registry()
    print(json.dumps(reg, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
