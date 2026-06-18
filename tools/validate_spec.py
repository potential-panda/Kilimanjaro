#!/usr/bin/env python3
"""Validate a Kilimanjaro hypothesis spec.

Two layers, matching requirements/hypothesis-contract.md:

1. Structural shape and enums - schemas/hypothesis_spec.schema.json (JSON Schema).
2. Cross-field business rules from the contract's "Validation Rules" section
   that JSON Schema can't express cleanly (e.g. "duplicate search was
   performed" spans three fields with no single type/enum check). This
   includes the BINANCE-default-for-JP-targets rule: per direct user
   instruction, BINANCE is the default feature source for any target with a
   confirmed lead-lag leader (see _KNOWN_LEAD_LAG_LEADER below), not merely
   an allowed option -- sourcing from the target's own market instead
   requires cross_market_rationale, except for the local-execution-mechanics
   families where BINANCE has no analogue.

"feature expression is not SQL over whitelisted Makalu datasets" is checked
in two halves: the *structural/safety* half (single scalar expression, no
joins/subqueries/DDL, allowlisted functions, bounded windows) is enforced
here via tools/sql_safety.py (task 6). The *column-whitelist* half --
confirming every referenced column actually resolves to a real Makalu
dataset column -- requires live schema knowledge and is deferred to task 5
alongside the other check intentionally not enforced here:
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

from sql_safety import validate_expression as _validate_sql_expression

_SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "hypothesis_spec.schema.json"

# Confirmed BINANCE-leads-JP-market pairs, per tools/lead_lag_wave.py's
# _SUPPORTED_PAIRS. BINANCE leads 81-97% of the time across every 30-minute
# JST bucket (markets/waves/leadlag_BINANCE_GMO_*.csv) -- per direct user
# instruction, BINANCE is now the *default* feature source for predicting
# these targets, not merely an allowed option requiring justification.
_KNOWN_LEAD_LAG_LEADER = {
    ("GMO", "BTC_JPY"): ("BINANCE", "BTC_USDT"),
    ("GMO", "ETH_JPY"): ("BINANCE", "ETH_USDT"),
    ("GMO", "XRP_JPY"): ("BINANCE", "XRP_USDT"),
}

# Families inherently about the target market's own local execution
# mechanics (how OUR resting order interacts with that market's own queue) --
# BINANCE has no analogue for these, so same-market sourcing is expected by
# default, not an exception needing justification.
_LOCAL_MECHANICS_FAMILIES = {"queue_depletion", "adverse_selection_avoidance"}


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

    sql_expression = h.get("feature", {}).get("sql_expression") or ""
    if not sql_expression.strip():
        errors.append("feature.sql_expression is empty")
    else:
        errors.extend(f"feature.sql_expression: {e}" for e in _validate_sql_expression(sql_expression))

    if not h.get("falsification_tests"):
        errors.append("falsification_tests is empty")

    target = h.get("target", {})
    if target.get("type") == "research_label":
        research_metadata = (h.get("data_requirements") or {}).get("research_metadata") or {}
        if not (research_metadata.get("param_id") or "").strip():
            errors.append(
                "target.type is 'research_label' but data_requirements.research_metadata.param_id is missing"
            )

    target_market = (target.get("source_dataset") or {}).get("exchange"), (target.get("source_dataset") or {}).get("symbol")
    feature_markets = {
        (ds.get("exchange"), ds.get("symbol")) for ds in h.get("feature", {}).get("source_datasets", [])
    }
    cross_market_rationale = (h.get("cross_market_rationale") or "").strip()
    if feature_markets - {target_market} and not cross_market_rationale:
        errors.append(
            "feature is sourced from a different market than the target "
            f"({target_market}) but cross_market_rationale is empty -- name which market the "
            "feature is actually computed on and why that market's signal is informative for "
            "the target market (e.g. a follower market's own order book is not independently "
            "informative when a lead-lag relationship makes it largely a copy of the leader's book)"
        )

    leader_market = _KNOWN_LEAD_LAG_LEADER.get(target_market)
    if (
        leader_market is not None
        and h.get("family") not in _LOCAL_MECHANICS_FAMILIES
        and leader_market not in feature_markets
        and not cross_market_rationale
    ):
        errors.append(
            f"target {target_market} has a confirmed lead-lag leader {leader_market} "
            f"({leader_market[0]} leads 81-97% of the time, per tools/lead_lag_wave.py) -- "
            "BINANCE is the default feature source for predicting this target; sourcing from "
            f"{target_market} instead requires cross_market_rationale explaining why BINANCE's "
            "signal isn't a better source for this specific hypothesis"
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
