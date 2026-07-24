"""Candidate tier: re-measure screening survivors on non-overlapping episodes.

The screening scan (fixed_horizon, per-second) inflates significance through
overlapping return windows, so it can only *screen* — it selects candidates by
rank stability across a train/holdout split, which overlap cannot fake
(doc/overlapping-returns.md). This module takes those rank-stable candidates and
re-measures each with EpisodeReturn: exactly one non-overlapping trade per
episode, real bid/ask execution, a true holding-period distribution. On
non-overlapping returns the plain significance test is legitimate again, so the
verdict's FDR runs on these honest numbers — never on the screening tier's.

This is step 9b/9c of the scanner loop: screening scan -> attach -> episode
re-test -> attach -> verdict. It runs only when screening produced at least one
rank-stable candidate, so most specs still write a single result folder.
"""

from __future__ import annotations

from pathlib import Path

from kilimanjaro.driver.scan_runner import CSV_FIELDS, _target_and_schedule
from kilimanjaro.return_calc.episode import EpisodeReturn
from kilimanjaro.driver.scan_runner import run_grid_scan
from kilimanjaro.signal.expression import ExpressionSignal

# Combo-identity fields on a screening row (ExpressionSignal.params), minus the
# label alias, used to recover which signals to re-measure.
_IDENTITY = ("threshold", "min_age", "max_age", "invert")


def _expr_by_label(spec: dict) -> dict[str, str]:
    """label (or raw expr) -> raw expr. A screening row stores `expr` as the
    label when one exists (ExpressionSignal.params), so map back to the formula."""
    out: dict[str, str] = {}
    for e in spec["expressions"]:
        raw = e["expr"]
        out[e.get("label") or raw] = raw
        out[raw] = raw
    return out


def build_episode_combos(spec: dict, candidate_rows: list[dict]) -> list[tuple]:
    """(ExpressionSignal, EpisodeReturn) for exactly the rank-stable candidates.

    Built directly from the survivor tuples — NOT via the cartesian build_combos
    — so the candidate tier measures the combos that actually survived, and no
    others. Each is swept over spec.max_holds (default: hold to natural episode
    end), the one dimension episode adds over fixed_horizon.
    """
    markets = spec["markets"]
    by_label = _expr_by_label(spec)
    max_holds = spec.get("max_holds", [None])

    seen: set[tuple] = set()
    combos: list[tuple] = []
    for r in candidate_rows:
        raw_expr = by_label.get(r["expr"])
        if raw_expr is None:  # row not from this spec — skip defensively
            continue
        key = (raw_expr, r["threshold"], r["min_age"], r["max_age"], r["invert"])
        if key in seen:
            continue
        seen.add(key)
        sig = ExpressionSignal.create(
            markets=markets,
            expr=raw_expr,
            threshold=r["threshold"],
            min_age=r["min_age"],
            max_age=r["max_age"],
            invert=bool(r["invert"]),
            label=r["expr"] if r["expr"] != raw_expr else None,
        )
        for mh in max_holds:
            combos.append((sig, EpisodeReturn(max_hold_seconds=mh)))
    return combos


def run_episode_retest(
    spec: dict,
    candidate_rows: list[dict],
    start: str,
    end: str,
    *,
    results_root: Path,
    screening_scan_id: str,
    split: str | None = None,
) -> tuple[dict, Path, str] | None:
    """Re-measure the rank-stable candidates under EpisodeReturn.

    Returns (res, out_dir, scan_id) for the episode scan, or None when there are
    no candidates to re-test (the common case — the caller then fails the spec
    at the screening tier). The episode scan lands in a sibling `*_episode`
    folder so the two tiers stay paired in cycle/13_results.
    """
    combos = build_episode_combos(spec, candidate_rows)
    if not combos:
        return None
    target, schedule = _target_and_schedule(spec)
    scan_id = f"{screening_scan_id}_episode"
    return run_grid_scan(
        combos,
        target,
        start,
        end,
        results_root=results_root,
        scan_name=scan_id,
        csv_fields=CSV_FIELDS,
        split=split,
        param_meta={"spec": spec, "tier": "episode", "screening_scan_id": screening_scan_id},
        schedule=schedule,
        costs_bps=spec.get("costs_bps"),
        scan_id=scan_id,
        out_dir=results_root / scan_id,
    )
