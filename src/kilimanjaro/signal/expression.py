"""ExpressionSignal: a SignalDef defined by a formula, not a new class.

The point of this module is to make the marginal cost of a new signal idea
approach zero: write an expression over the multi-market 1s feature frame,
sweep thresholds/age-windows/horizons through run_grid_split, done.

    ExpressionSignal(
        markets={"a": {...BINANCE ETH...}, "b": {...GMO ETH...}},
        expr="zscore(a_mid / b_mid, 3600)",
        threshold=2.0,
        invert=True,          # fade the signal (mean reversion)
        min_age=10, max_age=300,
    )

Safety: the expression is validated against an AST allowlist before
evaluation — only column names, numeric constants, arithmetic/comparison
operators, and calls to the registered causal ops (kilimanjaro.op.series.OPS,
plus threshold_age-based gating applied outside the formula) are accepted.
No attributes, subscripts, lambdas, comprehensions, or imports. This is the
same philosophy as the SQL AST allowlist in requirements/: parseable is not
the bar, explicitly-allowed is.

Causality: every registered op is causal (tested), and the frame itself is
built from at-or-before data, so any accepted expression is lookahead-free
by construction.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from kilimanjaro.data.feature_frame import load_feature_frame
from kilimanjaro.op.age import threshold_age, threshold_episode
from kilimanjaro.op.series import OPS

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")

_ALLOWED_NODES = (
    ast.Expression,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Call,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.USub,
    ast.UAdd,
    ast.Gt,
    ast.GtE,
    ast.Lt,
    ast.LtE,
    ast.Eq,
    ast.NotEq,
)


def validate_expr(expr: str) -> list[str]:
    """Validate against the AST allowlist; return the column names used.

    Raises ValueError on anything outside the allowlist: attribute access,
    subscripts, lambdas, keywords args to unknown places, boolean short-
    circuiting, star-args, f-strings, etc.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"expression does not parse: {e}") from e

    columns: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise TypeError(
                f"disallowed syntax {type(node).__name__!r} in expression: {expr}"
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in OPS:
                raise ValueError(
                    f"only registered ops {sorted(OPS)} may be called, got:"
                    f" {ast.dump(node.func)}"
                )
            if node.keywords:
                raise ValueError("keyword arguments are not allowed in expressions")
        if isinstance(node, ast.Name) and node.id not in OPS:
            columns.add(node.id)

    if not columns:
        raise ValueError(f"expression references no frame columns: {expr}")
    return sorted(columns)


def eval_expr(expr: str, frame: pd.DataFrame) -> pd.Series:
    """Evaluate a validated expression over the frame columns."""
    columns = validate_expr(expr)
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise ValueError(f"expression needs columns missing from frame: {missing}")
    namespace = {**OPS, **{c: frame[c] for c in columns}}
    return pd.Series(
        eval(
            compile(ast.parse(expr, mode="eval"), "<expr>", "eval"),
            {"__builtins__": {}},
            namespace,
        ),
        index=frame.index,
    )


def _freeze_markets(markets: dict[str, dict]) -> tuple:
    return tuple(
        (alias, m["exchange"], m["symbol"], m.get("execution_size"))
        for alias, m in sorted(markets.items())
    )


@dataclass(frozen=True)
class ExpressionSignal:
    """Signal that fires while |expr| > threshold, with optional age window.

    Threshold/age/episode semantics on the 1s grid (threshold_age on the 1s
    grid), applied to an arbitrary formula instead of a fixed OBI column.

    cache_key = (markets, columns the expression needs) — all thresholds,
    age windows, inversions, and even *different formulas over the same
    columns* share one frame load.

    invert=True flips direction and strength (fade the signal): downstream
    stats and IC then measure the mean-reversion variant consistently.
    """

    markets_key: tuple
    expr: str
    threshold: float
    min_age: int | None = None
    max_age: int | None = None
    invert: bool = False
    label: str | None = None

    @classmethod
    def create(
        cls,
        markets: dict[str, dict],
        expr: str,
        threshold: float,
        min_age: int | None = None,
        max_age: int | None = None,
        invert: bool = False,
        label: str | None = None,
    ) -> ExpressionSignal:
        validate_expr(expr)  # fail fast at construction
        return cls(
            markets_key=_freeze_markets(markets),
            expr=expr,
            threshold=threshold,
            min_age=min_age,
            max_age=max_age,
            invert=invert,
            label=label,
        )

    @property
    def params(self) -> dict:
        return {
            "expr": self.label or self.expr,
            "threshold": self.threshold,
            "min_age": self.min_age,
            "max_age": self.max_age,
            "invert": self.invert,
        }

    @property
    def columns(self) -> list[str]:
        return validate_expr(self.expr)

    @property
    def cache_key(self) -> tuple:
        return (self.markets_key, tuple(self.columns))

    def _markets(self) -> dict[str, dict]:
        return {
            alias: {"exchange": ex, "symbol": sym, "execution_size": size}
            for alias, ex, sym, size in self.markets_key
        }

    def load_data(
        self, start: str, end: str, data_root: Path = _DEFAULT_DATA_ROOT
    ) -> pd.DataFrame:
        return load_feature_frame(
            self._markets(), self.columns, start, end, data_root=data_root
        )

    def generate(self, frame: pd.DataFrame) -> pd.DataFrame:
        s = eval_expr(self.expr, frame)
        if self.invert:
            s = -s

        mask = s.abs() > self.threshold
        if self.min_age is not None or self.max_age is not None:
            age = threshold_age(s, self.threshold)
            if self.min_age is not None:
                mask &= age >= self.min_age
            if self.max_age is not None:
                mask &= age < self.max_age

        m = mask.fillna(False)
        active = frame.loc[m, ["ts"]].copy()
        active["direction"] = np.sign(s[m])
        active["strength"] = s[m]
        # Episode id ties every active second to its maximal run; EpisodeReturn
        # groups on it to sample one non-overlapping trade per episode. Computed
        # on the full |s|>threshold runs (before any age gating masks rows), so
        # the id is the true episode identity regardless of min_age/max_age.
        active["episode"] = threshold_episode(s, self.threshold)[m]
        return active.reset_index(drop=True)
