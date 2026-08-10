"""Threshold policy: turn a scan result into a pass/fail decision.

Two tiers (doc/overlapping-returns.md), because the screening scan measures
returns on an overlapping per-second grid and so cannot supply an honest p-value:

1. **Screening (fixed_horizon).** Overlap inflates significance, so the screen
   is *rank stability* across a train/holdout split — which overlap cannot fake.
   `select_candidates` returns the rank-stable combos. FDR is NOT applied here;
   correcting overlap-inflated p-values would be correcting the wrong quantity.
2. **Candidate (episode).** The rank-stable combos are re-measured on
   non-overlapping episode returns (driver/return_eval). Now the plain
   significance test is legitimate, so the FDR gate runs here. `evaluate_episode`
   PASSES a spec when at least one candidate is positive in BOTH windows AND
   survives Benjamini-Hochberg on the holdout episode p-value — with the family
   size set to the full screening grid, so the correction still accounts for
   every combo really searched.

Everything else (verdict mix, yellow flags) is surfaced in the review packet,
not used to auto-promote — promotion stays a human gate.

`evaluate` is the single-tier gate (rank-stable AND FDR on one result); it
predates the split and is kept for direct grid runs that do no episode re-test.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome:
    passed: bool
    status: str  # "scan-passed" | "scan-failed"
    headline: str
    n_stable: int  # rank-stable combos
    n_survivors: int  # confirmed survivors (rank-stable AND FDR-significant)
    n_combos: int
    verdicts: dict
    best: dict | None  # best confirmed survivor (or None)


def _best(rows: list[dict]) -> dict:
    return max(rows, key=lambda r: r.get("holdout_rank_pct") or 0)


def _headline(
    res: dict, survivors: list[dict], n_stable: int, best: dict | None
) -> str:
    n = res["params"]["n_combos"]
    if best is None:
        counts = res.get("verdict_counts", {})
        extra = ", ".join(
            f"{counts[v]} {v}" for v in ("decayed", "regime_dependent") if counts.get(v)
        )
        dropped = f"; {n_stable} rank-stable dropped by FDR" if n_stable else ""
        return f"0/{n} confirmed survivors — no candidates{dropped}" + (
            f" ({extra})" if extra else ""
        )
    keys = [
        k
        for k in best
        if not k.startswith(("train_", "holdout_"))
        and k
        not in ("stable", "mt_significant", "verdict", "n_combos_tested", "window")
    ]
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


def select_candidates(screening_res: dict) -> list[dict]:
    """Rank-stable combos from the screening tier — the candidate set fed to the
    episode re-test. Rank stability across the split is the overlap-robust
    screen; it is not the verdict, and FDR is deliberately not applied yet."""
    return list(screening_res.get("stable") or [])


def _best_by_net(rows: list[dict]) -> dict:
    # Episode-tier ranks are degenerate on a handful of candidates, so pick the
    # best survivor by realised holdout edge, not by rank percentile.
    return max(rows, key=lambda r: r.get("holdout_avg_net") or float("-inf"))


def _episode_headline(n_candidates: int, survivors: list[dict], best: dict | None) -> str:
    if best is None:
        return (
            f"0/{n_candidates} candidates confirmed on non-overlapping episodes "
            f"(positive both windows + FDR)"
        )
    hold = best.get("holdout_avg_hold_s")
    hold_txt = f" hold~{hold:.0f}s" if isinstance(hold, (int, float)) else ""
    return (
        f"{len(survivors)}/{n_candidates} confirmed on episodes; best: "
        f"expr={best.get('expr')} threshold={best.get('threshold')} "
        f"max_hold_s={best.get('max_hold_s')} "
        f"(holdout {best.get('holdout_avg_net', float('nan')):+.2f} bps "
        f"t={best.get('holdout_t_stat', float('nan')):+.2f} "
        f"n_ep={best.get('holdout_n')}{hold_txt})"
    )


def evaluate_episode(
    episode_res: dict,
    n_candidates: int,
    n_screening_combos: int,
    mt_alpha: float = 0.10,
) -> Outcome:
    """Final verdict from the candidate (episode) tier.

    A confirmed survivor is a candidate whose non-overlapping episode returns are
    positive in BOTH train and holdout AND FDR-significant on the holdout
    bootstrap p-value. BH's family size is the full screening grid
    (n_screening_combos), so relaxing the screening gate to rank-stable-only does
    not smuggle in a weaker multiple-testing correction.
    """
    from kilimanjaro.framework.evaluate import _bh_significant

    rows = episode_res.get("rows") or []
    pvals = [r.get("holdout_p_value_bootstrap") for r in rows]
    mt = _bh_significant(pvals, mt_alpha, m_total=n_screening_combos)

    survivors: list[dict] = []
    for r, sig in zip(rows, mt):
        r["mt_significant"] = bool(sig)
        if (
            sig
            and (r.get("train_avg_net") or 0) > 0
            and (r.get("holdout_avg_net") or 0) > 0
        ):
            survivors.append(r)

    best = _best_by_net(survivors) if survivors else None
    return Outcome(
        passed=bool(survivors),
        status="scan-passed" if survivors else "scan-failed",
        headline=_episode_headline(n_candidates, survivors, best),
        n_stable=n_candidates,
        n_survivors=len(survivors),
        n_combos=episode_res["params"]["n_combos"],
        verdicts=episode_res.get("verdict_counts", {}),
        best=best,
    )
