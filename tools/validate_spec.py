#!/usr/bin/env python3
"""Validate a Kilimanjaro hypothesis spec.

Two layers, matching requirements/hypothesis-contract.md:

1. Structural shape and enums - schemas/hypothesis_spec.schema.json (JSON Schema).
2. Cross-field business rules from the contract's "Validation Rules" section
   that JSON Schema can't express cleanly (e.g. "duplicate search was
   performed" spans three fields with no single type/enum check).

Two checks from the contract are intentionally not enforced here because
they require data this script doesn't have:
  - "feature expression is not SQL over whitelisted Makalu datasets" -- the
    SQL AST allowlist is task 6's scope; this only checks the expression is
    non-empty.
  - "data requirements cannot be matched to Makalu inventory" -- requires a
    live inventory snapshot (task 4); not checked here.
  - "target horizon is selected after scanning" is a procedural rule about
    *when* a human/agent edits the file, not something a static check on
    the file's content can detect.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import yaml

_SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "hypothesis_spec.schema.json"


class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text())


def _business_rule_errors(spec: dict) -> list[str]:
    """Cross-field rules from hypothesis-contract.md's "Validation Rules"."""
    errors: list[str] = []
    h = spec.get("hypothesis", {})

    if not (h.get("expected_edge_mechanism") or "").strip():
        errors.append("missing-mechanism: expected_edge_mechanism is empty")

    if h.get("family") == "custom" and not (h.get("custom_family_justification") or "").strip():
        errors.append("family is 'custom' but custom_family_justification is empty")

    dup = h.get("duplicate_search") or {}
    if (
        not dup.get("searched_at")
        or not dup.get("query_terms")
        or dup.get("conclusion") not in ("new", "variant", "duplicate")
    ):
        errors.append(
            "duplicate search was not performed: duplicate_search.searched_at, "
            "query_terms, and conclusion must all be set"
        )

    if not (h.get("feature", {}).get("sql_expression") or "").strip():
        errors.append("feature.sql_expression is empty")

    if not h.get("falsification_tests"):
        errors.append("falsification_tests is empty")

    target = h.get("target", {})
    if target.get("type") == "research_label":
        research_metadata = (h.get("data_requirements") or {}).get("research_metadata") or {}
        if not (research_metadata.get("param_id") or "").strip():
            errors.append(
                "target.type is 'research_label' but data_requirements.research_metadata.param_id is missing"
            )

    return errors


def validate(spec: dict) -> list[str]:
    """Return a list of validation error strings; empty list means valid."""
    schema = _load_schema()
    errors: list[str] = []

    validator = jsonschema.Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(spec), key=lambda e: list(e.absolute_path)):
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"schema: {path}: {err.message}")

    # Business rules assume the basic shape is present; skip them if the
    # schema already failed badly enough that hypothesis fields are missing.
    if "hypothesis" in spec and isinstance(spec["hypothesis"], dict):
        errors.extend(_business_rule_errors(spec))

    return errors


def validate_file(path: str) -> list[str]:
    spec = yaml.safe_load(Path(path).read_text())
    return validate(spec)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: validate_spec.py <path-to-spec.yaml>", file=sys.stderr)
        return 2

    errors = validate_file(sys.argv[1])
    if errors:
        print(f"INVALID: {sys.argv[1]}")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(f"VALID: {sys.argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
