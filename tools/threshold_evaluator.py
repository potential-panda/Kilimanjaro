#!/usr/bin/env python3
"""Threshold evaluator (task 8): applies the v1 pass/fail/yellow-flag rules
from requirements/requirements.md's "Deterministic Scanner Metrics" and
direction/evaluation-framework.md's "Pass / promote-to-backtest thresholds"
to a scan report produced by tools/scanner.py, filling in its
threshold_verdict (which scanner.py always leaves as "not_evaluated").

failed_rules use the exact decision_reason codes from
requirements/hypothesis-contract.md's "Decision Reasons" enum, so task 9's
ledger-update workflow can record threshold_verdict.failed_rules[0]
directly as a ledger event's decision_reason without re-mapping anything.

v1 scope notes (documented limitations, not silently assumed):
  - Look-ahead leakage, timestamp alignment, and missing-param_id checks
    are not re-verified here -- those are spec-validation concerns
    (tools/validate_spec.py) about the hypothesis as written, not
    something derivable from the scan report's numbers alone.
  - Regime-bucket stability is always flagged "regime-stability-unproven"
    in v1, because tools/scanner.py always emits stability_by_regime
    empty (documented v1 gap in that module) -- not computed, not assumed
    passing.
  - The cost buffer's spread cost comes from report.market_context.avg_half_spread_bps,
    measured directly by tools/scanner.py from the target market's own data
    over the exact scanned window -- not declared in the spec ahead of
    time. A report missing it (shouldn't happen with a current scanner.py,
    but checked rather than assumed) is marked needs-tooling for that
    check specifically, not silently passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_MIN_DISTINCT_DAYS = 5
_MIN_RAW_SAMPLES = 10_000
_MIN_EFFECTIVE_SAMPLES = 1_000
_MIN_ABS_IC = 0.02
_MIN_HIT_RATE = 0.51
_COST_BUFFER_SAFETY_MULTIPLIER = 1.25
_MIN_STABLE_DAYS = 3
_MIN_STABLE_DAY_FRACTION = 0.60

# Exact codes from requirements/hypothesis-contract.md's Decision Reasons enum.
_REASON_INSUFFICIENT_DATA = "insufficient-data"
_REASON_FAILED_IC = "failed-ic-threshold"
_REASON_FAILED_HIT_RATE = "failed-hit-rate-threshold"
_REASON_FAILED_COST_BUFFER = "failed-cost-buffer"
_REASON_AUTOCORR_FAILED = "autocorrelation-adjustment-failed"
_REASON_PARAMETER_OVERFIT = "parameter-overfit"


class Verdict:
    def __init__(self):
        self.failed_rules: list[str] = []
        self.yellow_flags: list[str] = []
        self.notes: list[str] = []
        self.needs_more_data = False
        self.needs_tooling = False
        self.cost_buffer: dict | None = None

    def fail(self, reason: str, note: str) -> None:
        self.failed_rules.append(reason)
        self.notes.append(note)

    def flag(self, name: str, note: str) -> None:
        self.yellow_flags.append(name)
        self.notes.append(note)

    @property
    def status(self) -> str:
        if self.needs_more_data:
            return "needs-more-data"
        if self.needs_tooling:
            return "needs-tooling"
        return "fail" if self.failed_rules else "pass"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "failed_rules": self.failed_rules,
            "yellow_flags": self.yellow_flags,
            "verdict_text": " ".join(self.notes) if self.notes else "all v1 thresholds cleared",
            "cost_buffer": self.cost_buffer,
        }


def _check_distinct_days(report: dict, v: Verdict) -> None:
    days = report["data_window"]["distinct_days"]
    if days < _MIN_DISTINCT_DAYS:
        v.needs_more_data = True
        v.fail(_REASON_INSUFFICIENT_DATA, f"only {days} distinct days scanned, need at least {_MIN_DISTINCT_DAYS}")


def _check_sample_counts(report: dict, v: Verdict) -> None:
    m = report["metrics"]
    if m["raw_sample_count"] < _MIN_RAW_SAMPLES:
        v.fail(_REASON_INSUFFICIENT_DATA, f"raw_sample_count {m['raw_sample_count']} below minimum {_MIN_RAW_SAMPLES}")
    if m["effective_sample_count"] < _MIN_EFFECTIVE_SAMPLES:
        v.fail(_REASON_INSUFFICIENT_DATA, f"effective_sample_count {m['effective_sample_count']:.1f} below minimum {_MIN_EFFECTIVE_SAMPLES}")


def _check_ic(report: dict, v: Verdict) -> None:
    ic = report["metrics"]["ic_pooled"]
    if ic is None or abs(ic) < _MIN_ABS_IC:
        v.fail(_REASON_FAILED_IC, f"pooled IC {ic} below minimum magnitude {_MIN_ABS_IC}")


def _check_hit_rate(report: dict, v: Verdict) -> None:
    hr = report["metrics"]["hit_rate"]
    if hr is None or hr < _MIN_HIT_RATE:
        v.fail(_REASON_FAILED_HIT_RATE, f"hit rate {hr} below minimum {_MIN_HIT_RATE}")


def _check_significance(report: dict, v: Verdict) -> None:
    m = report["metrics"]
    ic = m["ic_pooled"]
    ci_low, ci_high = m.get("bootstrap_ci_low"), m.get("bootstrap_ci_high")
    if ic is None or ci_low is None or ci_high is None:
        v.fail(_REASON_AUTOCORR_FAILED, "significance interval unavailable")
        return
    same_sign_as_ic = (ci_low > 0 and ci_high > 0) if ic > 0 else (ci_low < 0 and ci_high < 0)
    if not same_sign_as_ic:
        v.fail(_REASON_AUTOCORR_FAILED, f"significance interval [{ci_low:.4f}, {ci_high:.4f}] does not exclude zero with the claimed sign")


def _check_day_stability(report: dict, v: Verdict) -> None:
    m = report["metrics"]
    ic, ic_by_day = m["ic_pooled"], m["ic_by_day"]
    if ic is None or not ic_by_day:
        return
    claimed_sign = 1 if ic > 0 else -1
    days = list(ic_by_day.values())
    matching = sum(1 for day_ic in days if (1 if day_ic > 0 else -1) == claimed_sign)
    fraction = matching / len(days)
    if matching < _MIN_STABLE_DAYS or fraction < _MIN_STABLE_DAY_FRACTION:
        v.fail(
            _REASON_PARAMETER_OVERFIT,
            f"sign matched claimed direction on only {matching}/{len(days)} days ({fraction:.0%}), "
            f"need at least {_MIN_STABLE_DAYS} days and {_MIN_STABLE_DAY_FRACTION:.0%}",
        )


def _check_single_cell_overfit(report: dict, v: Verdict) -> None:
    grid = sorted(report["metrics"]["decay_by_horizon"], key=lambda d: d["horizon_seconds"])
    primary = report["metrics"]["primary_horizon_seconds"]
    primary_idx = next((i for i, d in enumerate(grid) if d["horizon_seconds"] == primary), None)
    primary_ic = grid[primary_idx]["ic_pooled"] if primary_idx is not None else None
    if primary_idx is None or primary_ic is None:
        return

    neighbors = [grid[i] for i in (primary_idx - 1, primary_idx + 1) if 0 <= i < len(grid)]
    if not neighbors:
        v.flag("insufficient-grid-for-overfit-check", "only one horizon was tested; cannot check neighboring cells")
        return

    claimed_sign = 1 if primary_ic > 0 else -1
    matching = sum(1 for n in neighbors if n["ic_pooled"] is not None and (1 if n["ic_pooled"] > 0 else -1) == claimed_sign)
    required = 2 if len(neighbors) >= 2 else 1
    if matching < required:
        v.fail(_REASON_PARAMETER_OVERFIT, f"only {matching}/{len(neighbors)} neighboring horizon cells share the primary cell's sign")


def _check_cost_buffer(report: dict, spec: dict, v: Verdict) -> None:
    h = spec["hypothesis"]
    ea = h.get("execution_assumptions", {})

    half_spread_bps = (report.get("market_context") or {}).get("avg_half_spread_bps")
    if half_spread_bps is None:
        v.needs_tooling = True
        v.fail(
            _REASON_FAILED_COST_BUFFER,
            "report.market_context.avg_half_spread_bps is missing -- cannot evaluate the cost "
            "buffer without a measured spread magnitude",
        )
        return

    buckets = sorted(report["metrics"]["bucketed_returns"], key=lambda b: b["bucket"])
    if len(buckets) < 2:
        v.flag("insufficient-buckets-for-cost-buffer", "fewer than 2 buckets; cost buffer not evaluated")
        return

    edge_fraction = (buckets[-1]["mean_target_return"] - buckets[0]["mean_target_return"]) / 2
    edge_bps = abs(edge_fraction) * 10000

    fees_bps = ea.get("fees_bps") or 0
    slippage_bps = ea.get("slippage_bps") or 0
    # half_spread_bps is measured directly from the target market over this
    # exact scan window (tools/scanner.py's market_context), not declared in
    # the spec -- removes the staleness risk a spec-declared value had (see
    # requirements.md's 2026-06-19 cost-buffer note).
    half_spread_cost_bps = 0 if (h["target"]["type"] == "spread_adjusted_return") else half_spread_bps
    # latency_penalty_bps (observed adverse mid movement over latency_ms) is
    # not computed in v1 -- documented gap, not assumed zero-cost beyond
    # what's already in fees/spread/slippage.
    required_bps = (fees_bps + half_spread_cost_bps + slippage_bps) * _COST_BUFFER_SAFETY_MULTIPLIER

    v.cost_buffer = {
        "edge_bps": edge_bps,
        "fee_bps": fees_bps,
        "half_spread_bps": half_spread_cost_bps,
        "slippage_bps": slippage_bps,
        "safety_multiplier": _COST_BUFFER_SAFETY_MULTIPLIER,
        "required_bps": required_bps,
        "cleared": edge_bps > required_bps,
    }

    if edge_bps <= required_bps:
        v.fail(
            _REASON_FAILED_COST_BUFFER,
            f"edge {edge_bps:.3f}bps does not exceed required cost buffer {required_bps:.3f}bps "
            f"(fees {fees_bps} + half_spread {half_spread_cost_bps:.3f} (measured) + slippage {slippage_bps}, x{_COST_BUFFER_SAFETY_MULTIPLIER})",
        )


def evaluate(report: dict, spec: dict) -> dict:
    """Returns a new threshold_verdict dict; does not mutate the report."""
    v = Verdict()
    _check_distinct_days(report, v)
    _check_sample_counts(report, v)
    _check_ic(report, v)
    _check_hit_rate(report, v)
    _check_significance(report, v)
    _check_day_stability(report, v)
    _check_single_cell_overfit(report, v)
    _check_cost_buffer(report, spec, v)
    # stability_by_regime is always empty per tools/scanner.py's documented
    # v1 gap -- always disclosed as unproven, never silently assumed fine.
    v.flag("regime-stability-unproven", "stability_by_regime is not yet computed (scanner v1 gap)")
    return v.to_dict()


def main() -> int:
    import yaml

    parser = argparse.ArgumentParser(description="Evaluate a scan report against v1 thresholds")
    parser.add_argument("--report", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--write", action="store_true", help="Write the verdict back into the report file")
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text())
    spec = yaml.safe_load(Path(args.spec).read_text())

    verdict = evaluate(report, spec)
    print(json.dumps(verdict, indent=2))

    if args.write:
        report["threshold_verdict"] = verdict
        Path(args.report).write_text(json.dumps(report, indent=2))
        print(f"\nWrote verdict into {args.report}")

    return 0 if verdict["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
