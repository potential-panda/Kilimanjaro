"""Threshold policy: turn a scan result into a pass/fail decision.

A spec PASSES if at least one combo is a **confirmed survivor** — rank-stable
across train/holdout (the `stable` flag) AND surviving the Benjamini-Hochberg
multiple-testing correction (`mt_significant`). Requiring both is Gap 2: rank
stability alone lets lucky combos through once a grid is large, so the FDR filter
tightens the bar as the number of combos grows. Everything else (verdict mix,
yellow flags) is surfaced in the review packet, not used to auto-promote —
promotion stays a human gate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome:
    passed: bool
    status: str            # "scan-passed" | "scan-failed"
    headline: str
    n_stable: int          # rank-stable combos
    n_survivors: int       # confirmed survivors (rank-stable AND FDR-significant)
    n_combos: int
    verdicts: dict
    best: dict | None      # best confirmed survivor (or None)


def _best(rows: list[dict]) -> dict:
    return max(rows, key=lambda r: (r.get("holdout_rank_pct") or 0))


def _headline(res: dict, survivors: list[dict], n_stable: int, best: dict | None) -> str:
    n = res["params"]["n_combos"]
    if best is None:
        counts = res.get("verdict_counts", {})
        extra = ", ".join(
            f"{counts[v]} {v}" for v in ("decayed", "regime_dependent") if counts.get(v)
        )
        dropped = (f"; {n_stable} rank-stable dropped by FDR" if n_stable else "")
        return f"0/{n} confirmed survivors — no candidates{dropped}" + (
            f" ({extra})" if extra else "")
    keys = [k for k in best if not k.startswith(("train_", "holdout_"))
            and k not in ("stable", "mt_significant", "verdict", "n_combos_tested", "window")]
    desc = " ".join(f"{k}={best[k]}" for k in sorted(keys) if best.get(k) is not None)
    return (
        f"{len(survivors)}/{n} confirmed (rank-stable + FDR); best: {desc}"
        f" (holdout {best.get('holdout_avg_net', float('nan')):+.2f} bps"
        f" t={best.get('holdout_t_stat', float('nan')):+.2f} n={best.get('holdout_n')})"
    )


def evaluate(res: dict) -> Outcome:
    """Decide scan-passed vs scan-failed from a run_grid_split result.

    A confirmed survivor is rank-stable AND FDR-significant (mt_significant).
    """
    stable = res.get("stable") or []
    survivors = [r for r in stable if r.get("mt_significant")]
    best = _best(survivors) if survivors else None
    return Outcome(
        passed=bool(survivors),
        status="scan-passed" if survivors else "scan-failed",
        headline=_headline(res, survivors, len(stable), best),
        n_stable=len(stable),
        n_survivors=len(survivors),
        n_combos=res["params"]["n_combos"],
        verdicts=res.get("verdict_counts", {}),
        best=best,
    )
