#!/usr/bin/env python3
"""SQL AST allowlist for hypothesis spec feature expressions (task 6).

Implements requirements/requirements.md's "Scanner SQL Safety Contract":
a feature.sql_expression must be a single scalar expression, not a full
SELECT statement -- no joins, no subqueries, no DDL/DML, only a small
allowlisted set of scalar functions, and any window function must use a
finite, preceding-only frame.

This is enforced as an AST walk, not "parse and catch exceptions" --
confirmed during the task 6 feasibility spike that sqlglot parses
subqueries and multi-statement strings like "1; DROP TABLE trade;"
*without raising an error*, so checking for a parse exception alone proves
nothing about safety. Likewise, unrecognized functions (e.g. now()) surface
as a generic Anonymous node with no distinct type, and bounded vs. unbounded
window frames share the same top-level Window node type -- both need
structural inspection, not a type check alone.

This module deliberately does NOT check that column names resolve to a
whitelisted Makalu dataset/column -- that requires live schema knowledge
(task 5's territory, tied to tools/inventory.py) and is out of scope here.
"""

from __future__ import annotations

import sys

import sqlglot
from sqlglot import exp

# Per the contract's "Allowed AST node categories": arithmetic, comparisons,
# boolean logic, CASE WHEN, casts, column/literal references are all
# allowed implicitly (they're just not in the disallowed list below).
# Anything matching one of these classes -- anywhere in the tree, not just
# at the root -- is rejected outright.
_DISALLOWED_NODE_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.Subquery,
    exp.With,
    exp.CTE,
    exp.Join,
    exp.Drop,
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Pragma,
    exp.Copy,
    exp.Attach,
    exp.Command,
)

# Per the contract's "scalar functions: abs, sqrt, ln, log, exp, least,
# greatest, coalesce, nullif, sign, clip". Checked against the function's
# canonical name -- the sqlglot class name (lowercased) for functions with a
# dedicated node type, or the literal call name for generic Anonymous nodes
# (confirmed during the spike: now(), clip(), and other non-builtin calls
# all surface as Anonymous with no distinct type, so the allowlist can't
# rely on type checks alone for those).
_ALLOWED_SCALAR_FUNCTION_NAMES = {
    "abs", "sqrt", "ln", "log", "exp", "least", "greatest",
    "coalesce", "nullif", "sign", "clip",
}

# The contract's scalar-function list is for non-windowed calls. A window
# function (confirmed real usage in tools/volatility_wave.py and friends:
# rolling MIN/MAX/AVG over a bounded frame) is a different kind of call --
# checked separately, against the aggregate that's actually meaningful to
# roll over a frame, not the scalar-transform list above.
_ALLOWED_WINDOW_FUNCTION_NAMES = {"avg", "min", "max", "sum", "count"}

# CASE WHEN ... THEN ... ELSE ... END (and the equivalent IF construct some
# dialects normalize it to) is its own always-allowed category per the
# contract, not a "scalar function" -- confirmed both exp.Case and exp.If
# are subclasses of exp.Func, so they must be exempted explicitly or the
# function-name allowlist would wrongly reject every CASE WHEN expression.
_STRUCTURAL_FUNC_TYPES: tuple[type[exp.Expression], ...] = (exp.Case, exp.If)


class SqlSafetyError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _func_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return type(node).__name__.lower()


def _window_frame_errors(node: exp.Window) -> list[str]:
    """Per the contract's "Window policy": the frame must be finite and
    strictly backward-looking -- never reach into the future relative to
    the current row. An omitted frame, an UNBOUNDED bound on either side,
    or anything ending with FOLLOWING is rejected.

    The end bound doesn't have to be the literal CURRENT ROW: "N PRECEDING
    AND M PRECEDING" (M <= N) is also strictly in the past -- e.g. it's
    exactly how a one-row lag ("the previous tick's value") is expressed
    using only the allowed window-aggregate functions, with no separate
    LAG()/LEAD() function needed. Confirmed this shape is real and useful
    while building the first bootstrap hypotheses (task 11) that needed a
    point-in-time-safe lag, not just a rolling current-inclusive average.
    """
    spec = node.args.get("spec")
    if spec is None:
        return ["window function has no explicit frame (omitted frames are rejected; must be bounded and preceding-only)"]

    errors = []
    if spec.args.get("start_side") != "PRECEDING" or spec.args.get("start") == "UNBOUNDED":
        errors.append("window frame start must be a finite N PRECEDING, not UNBOUNDED or FOLLOWING")

    end, end_side = spec.args.get("end"), spec.args.get("end_side")
    end_is_safe = end == "CURRENT ROW" or (end_side == "PRECEDING" and end != "UNBOUNDED")
    if not end_is_safe:
        errors.append("window frame end must be CURRENT ROW or a finite N PRECEDING, not FOLLOWING or UNBOUNDED")
    return errors


def validate_expression(sql: str) -> list[str]:
    """Return a list of safety violations; empty list means safe.

    Does not raise on its own for a parse failure -- a ParseError is
    itself reported as a single error string, same as any other violation.
    """
    if not (sql or "").strip():
        return ["expression is empty"]

    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        return [f"could not parse as SQL: {exc}"]

    errors: list[str] = []

    # A Block means more than one statement was parsed (e.g. "1; DROP TABLE
    # trade;") -- sqlglot does not raise for this, so it must be checked
    # explicitly rather than assumed away by a successful parse.
    if isinstance(parsed, exp.Block) or isinstance(parsed, exp.Command):
        errors.append("expression must be a single statement, not multiple statements or a command")
        return errors

    if isinstance(parsed, _DISALLOWED_NODE_TYPES):
        errors.append(f"expression itself is a disallowed construct: {type(parsed).__name__}")

    # Identify which Func nodes are the function directly being windowed
    # (e.g. the Avg in "avg(x) OVER (...)") by object identity, so they're
    # checked against the window-function allowlist instead of the
    # scalar-function one.
    windowed_func_ids = {id(win.this) for win in parsed.find_all(exp.Window)}

    for node in parsed.walk():
        node = node[0] if isinstance(node, tuple) else node

        if isinstance(node, _DISALLOWED_NODE_TYPES):
            errors.append(f"disallowed construct found: {type(node).__name__}")
            continue

        if isinstance(node, exp.Func) and not isinstance(node, (exp.Window, *_STRUCTURAL_FUNC_TYPES)):
            name = _func_name(node)
            if id(node) in windowed_func_ids:
                if name not in _ALLOWED_WINDOW_FUNCTION_NAMES:
                    errors.append(f"window function '{name}' is not in the allowed list ({sorted(_ALLOWED_WINDOW_FUNCTION_NAMES)})")
            elif name not in _ALLOWED_SCALAR_FUNCTION_NAMES:
                errors.append(f"function '{name}' is not in the allowed list ({sorted(_ALLOWED_SCALAR_FUNCTION_NAMES)})")

        if isinstance(node, exp.Window):
            errors.extend(_window_frame_errors(node))

    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: sql_safety.py '<sql expression>'", file=sys.stderr)
        return 2

    errors = validate_expression(sys.argv[1])
    if errors:
        print("UNSAFE:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("SAFE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
