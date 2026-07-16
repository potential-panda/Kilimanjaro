"""Validate a scan-spec before the driver spends a scan on it.

This is the current-architecture validator (expressions over the feature frame),
replacing the retired SQL-spec validate_spec.py. It checks structure, expression
safety (AST allowlist), and that every referenced column names a declared market
alias. It does NOT check live feature availability — that is a data-readiness
concern handled separately (needs-more-data), because it requires an inventory
snapshot.
"""

from __future__ import annotations

from kilimanjaro.signal.expression import validate_expr

_VALID_RETURN_CALCS = ("fixed_horizon", "episode")


def validate_scan_spec(spec: dict) -> list[str]:
    """Return a list of human-readable errors; empty means valid."""
    errors: list[str] = []

    if not isinstance(spec, dict):
        return ["spec is not a mapping"]

    if not spec.get("hypothesis_id"):
        errors.append("missing 'hypothesis_id' (the driver must attribute the scan)")

    markets = spec.get("markets")
    if not isinstance(markets, dict) or not markets:
        errors.append("missing or empty 'markets'")
        markets = {}
    else:
        for alias, m in markets.items():
            for field in ("exchange", "symbol"):
                if not isinstance(m, dict) or not m.get(field):
                    errors.append(f"market '{alias}' missing '{field}'")

    target = spec.get("target")
    if not isinstance(target, dict):
        errors.append("missing 'target'")
    else:
        for field in ("exchange", "symbol", "execution_size"):
            if not target.get(field):
                errors.append(f"target missing '{field}'")

    return_calc = spec.get("return_calc", "fixed_horizon")
    if return_calc not in _VALID_RETURN_CALCS:
        errors.append(f"return_calc '{return_calc}' not in {_VALID_RETURN_CALCS}")
    elif return_calc == "fixed_horizon" and not spec.get("horizons"):
        errors.append("fixed_horizon return_calc needs a non-empty 'horizons'")

    if not spec.get("thresholds"):
        errors.append("missing or empty 'thresholds'")

    expressions = spec.get("expressions")
    if not isinstance(expressions, list) or not expressions:
        errors.append("missing or empty 'expressions'")
        expressions = []

    for e in expressions:
        label = (e or {}).get("label") or (e or {}).get("expr") or "<expr>"
        expr = (e or {}).get("expr")
        if not expr:
            errors.append(f"expression '{label}' missing 'expr'")
            continue
        try:
            cols = validate_expr(expr)
        except ValueError as ex:
            errors.append(f"expression '{label}': {ex}")
            continue
        for col in cols:
            alias = col.split("_", 1)[0]
            if alias not in markets:
                errors.append(
                    f"expression '{label}': column '{col}' references unknown"
                    f" market alias '{alias}' (declared: {sorted(markets)})"
                )

    return errors
