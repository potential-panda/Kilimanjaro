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


def idea_consistency_errors(specs: list[dict]) -> list[str]:
    """Enforce the one-idea-many-specs invariant across a set of scan specs.

    A *hypothesis* is an idea — an alpha/formula/expression. A *spec* is one
    concrete, executable rendering of it: a market (leader → target) plus a
    parameter grid. So the same idea tested on ETH and on BTC is ONE hypothesis
    with two specs, never two hypotheses.

    The idea is identified by `driver.generate.idea_key` (family + the set of
    numeral-blanked expression shapes; market, target, thresholds and horizons
    are *execution*, not idea). The invariant this checks, both directions:

      1. one idea -> one hypothesis id  (an idea must not be split across ids)
      2. one hypothesis id -> one idea  (an id must not cover two ideas)

    Returns a list of human-readable violations (empty = consistent). The
    generator already assigns ids this way; this is the guard that keeps a
    hand-added or hand-edited spec from silently re-fragmenting the registry.
    """
    from kilimanjaro.driver.generate import idea_key

    idea_to_ids: dict[str, set[str]] = {}
    id_to_ideas: dict[str, set[str]] = {}
    for s in specs:
        if not (isinstance(s, dict) and s.get("expressions")):
            continue
        hid = s.get("hypothesis_id")
        if not hid:
            continue
        k = idea_key(s)
        idea_to_ids.setdefault(k, set()).add(hid)
        id_to_ideas.setdefault(hid, set()).add(k)

    errors: list[str] = []
    for k, ids in sorted(idea_to_ids.items()):
        if len(ids) > 1:
            errors.append(
                f"one idea split across {len(ids)} hypotheses "
                f"{sorted(ids)} — idea {k} must be a single hypothesis with "
                f"one spec per market"
            )
    for hid, ideas in sorted(id_to_ideas.items()):
        if len(ideas) > 1:
            errors.append(
                f"hypothesis {hid} covers {len(ideas)} distinct ideas "
                f"{sorted(ideas)} — split them into one hypothesis each"
            )
    return errors
