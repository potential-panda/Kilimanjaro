"""Threshold policy: turn a scan result into a pass/fail decision.

The framework already encodes the rigor (train/holdout rank stability, positive
net in both windows, min_n) in the `stable` flag. The driver's policy is
deliberately thin on top of it: a spec PASSES if at least one combo is
rank-stable, and FAILS otherwise. Everything else (verdict mix, yellow flags,
cost buffer) is surfaced in the review packet for the human, not used to
auto-promote — promotion stays a human gate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome:
    passed: bool
    status: str            # "scan-passed" | "scan-failed"
    headline: str
    n_stable: int
    n_combos: int
    verdicts: dict
    best: dict | None      # best stable row (or None)


def _best(stable: list[dict]) -> dict:
    return max(stable, key=lambda r: (r.get("holdout_rank_pct") or 0))


def _headline(res: dict, best: dict | None) -> str:
    n = res["params"]["n_combos"]
    if best is None:
        counts = res.get("verdict_counts", {})
        extra = ", ".join(
            f"{counts[v]} {v}" for v in ("decayed", "regime_dependent") if counts.get(v)
        )
        return f"0/{n} rank-stable — no candidates" + (f" ({extra})" if extra else "")
    keys = [k for k in best if not k.startswith(("train_", "holdout_"))
            and k not in ("stable", "verdict", "n_combos_tested", "window")]
    desc = " ".join(f"{k}={best[k]}" for k in sorted(keys) if best.get(k) is not None)
    return (
        f"{sum(1 for r in res['rows'] if r['stable'])}/{n} rank-stable; best: {desc}"
        f" (holdout {best.get('holdout_avg_net', float('nan')):+.2f} bps"
        f" t={best.get('holdout_t_stat', float('nan')):+.2f} n={best.get('holdout_n')})"
    )


def evaluate(res: dict) -> Outcome:
    """Decide scan-passed vs scan-failed from a run_grid_split result."""
    stable = res.get("stable") or []
    best = _best(stable) if stable else None
    return Outcome(
        passed=bool(stable),
        status="scan-passed" if stable else "scan-failed",
        headline=_headline(res, best),
        n_stable=len(stable),
        n_combos=res["params"]["n_combos"],
        verdicts=res.get("verdict_counts", {}),
        best=best,
    )
