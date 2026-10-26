"""The scanner loop: sequence many specs through scan -> verdict -> ledger,
unattended and restart-safe.

process_spec() runs the full steps 1-4 pipeline for one spec. run_once()
walks a queue directory and processes each pending spec, isolating failures so
one bad spec never stops the loop. The cadence wraps run_once() in a forever loop;
run_once is one robust pass over the queue.

Dedup and durability come from the ledger (see ledger_io): a spec whose
fingerprint is already recorded is skipped, so re-running the loop is idempotent.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from kilimanjaro.driver import ledger_io
from kilimanjaro.driver.policy import (
    Outcome,
    evaluate_episode,
    select_candidates,
)
from kilimanjaro.driver.return_eval import run_episode_retest
from kilimanjaro.driver.review_packet import write_review_packet
from kilimanjaro.driver.scan_runner import run_spec
from kilimanjaro.driver.validate import validate_scan_spec

LOG = logging.getLogger("kilimanjaro.driver")

_DEFAULT_RESULTS_ROOT = Path("cycle/15_return_eval/grid_scan/horizon")


def process_spec(
    spec: dict,
    start: str,
    end: str,
    *,
    scan_name: str = "scan",
    actor_id: str = "scan-loop",
    split: str | None = None,
    results_root: Path = _DEFAULT_RESULTS_ROOT,
    dry_run: bool = False,
    ledger_path: Path | None = None,
) -> dict:
    """Run steps 1-4 for one spec. Returns a result dict with a 'status' key:
    skipped-duplicate | invalid-spec | dry-run | scan-passed | scan-failed | error.
    Never raises for spec-level problems; only programmer errors propagate.
    """
    lp = ledger_path or ledger_io.DEFAULT_LEDGER_PATH
    # per-spec window override, else the loop-wide window
    s = spec.get("start", start)
    e = spec.get("end", end)
    hyp = spec.get("hypothesis_id")
    fp = ledger_io.scan_fingerprint(spec, s, e)
    base = {
        "hypothesis_id": hyp,
        "scan_name": scan_name,
        "fingerprint": fp,
        "window": [s, e],
    }

    if ledger_io.already_processed(fp, lp):
        return {**base, "status": "skipped-duplicate"}

    errors = validate_scan_spec(spec)
    if errors:
        if hyp and not dry_run:
            ledger_io.record_status(
                hyp,
                "invalid-spec",
                actor_id,
                details={"scan_fingerprint": fp, "errors": errors},
                ledger_path=lp,
            )
        return {**base, "status": "invalid-spec", "errors": errors}

    if dry_run:
        return {**base, "status": "dry-run"}

    try:
        # draft -> scan-ready (validated + not a duplicate)
        ledger_io.record_status(
            hyp,
            "scan-ready",
            actor_id,
            details={"scan_fingerprint": fp, "window": [s, e], "spec_name": scan_name},
            ledger_path=lp,
        )
        # Step 8-9: screening scan (fixed_horizon, per-second) + attach.
        res, out_dir, scan_id = run_spec(
            spec, s, e, results_root, split=split, scan_name=scan_name
        )
        t = spec["target"]
        market = f"{t['exchange']}/{t['symbol']}"
        ledger_io.record_scan_attached(
            hyp, actor_id, scan_id, out_dir, market, res, ledger_path=lp
        )

        # Step 9b-9c: re-measure rank-stable candidates on non-overlapping
        # episodes (the honest tier), then attach. The verdict is decided on
        # THESE numbers; the screening scan only nominates candidates.
        candidates = select_candidates(res)
        ep_dir = None
        if candidates:
            ep = run_episode_retest(
                spec,
                candidates,
                s,
                e,
                results_root=results_root,
                screening_scan_id=scan_id,
                split=split,
            )
            if ep is not None:
                ep_res, ep_dir, ep_scan_id = ep
                ledger_io.record_scan_attached(
                    hyp, actor_id, ep_scan_id, ep_dir, market, ep_res, ledger_path=lp
                )
                outcome = evaluate_episode(
                    ep_res, len(candidates), res["params"]["n_combos"]
                )
            else:
                outcome = evaluate_episode(
                    {"rows": [], "params": {"n_combos": 0}},
                    len(candidates),
                    res["params"]["n_combos"],
                )
        else:
            # No rank-stable candidate — fail at the screening tier, no episode run.
            outcome = Outcome(
                passed=False,
                status="scan-failed",
                headline=f"0/{res['params']['n_combos']} rank-stable candidates "
                f"— no episode re-test",
                n_stable=0,
                n_survivors=0,
                n_combos=res["params"]["n_combos"],
                verdicts=res.get("verdict_counts", {}),
                best=None,
            )

        # Publish the packet before the terminal event so a failed publication
        # remains retryable rather than permanently losing a survivor's evidence.
        packet = None
        if outcome.passed:
            packet = write_review_packet(spec, res, outcome, out_dir, s, e, fp)

        # Step 10-11: verdict.
        ledger_io.record_status(
            hyp,
            outcome.status,
            actor_id,
            from_status="scan-ready",
            details={
                "scan_fingerprint": fp,
                "scan_id": scan_id,
                "scan_folder": str(out_dir),
                "episode_folder": str(ep_dir) if ep_dir else None,
                "headline": outcome.headline,
            },
            ledger_path=lp,
        )
        return {
            **base,
            "status": outcome.status,
            "headline": outcome.headline,
            "n_stable": outcome.n_stable,
            "n_survivors": outcome.n_survivors,
            "scan_folder": str(out_dir),
            "episode_folder": str(ep_dir) if ep_dir else None,
            "review_packet": str(packet) if packet else None,
        }
    except Exception as ex:
        LOG.exception("scan failed for %s (%s)", scan_name, hyp)
        # No terminal status recorded -> retryable when the cause (e.g. missing
        # data) clears. The scan-ready attempt does not suppress a retry.
        return {**base, "status": "error", "error": f"{type(ex).__name__}: {ex}"}


def run_once(
    queue_dir: Path,
    start: str,
    end: str,
    *,
    actor_id: str = "scan-loop",
    split: str | None = None,
    results_root: Path = _DEFAULT_RESULTS_ROOT,
    limit: int | None = None,
    dry_run: bool = False,
    ledger_path: Path | None = None,
    on_result=None,
) -> dict:
    """One pass over queue_dir/*.yaml. Returns a summary dict."""
    queue_dir = Path(queue_dir)
    specs = sorted(queue_dir.glob("*.yaml")) + sorted(queue_dir.glob("*.yml"))

    # One idea -> one hypothesis (many specs). Warn (don't block) if the queue
    # breaks it — the generator never does; a hand-added spec might.
    from kilimanjaro.driver.validate import idea_consistency_errors

    loaded = []
    for p in specs:
        try:
            loaded.append(yaml.safe_load(p.read_text()))
        except Exception as exc:  # noqa: BLE001 — processing reports the per-spec error later
            LOG.warning(
                "Skipping unreadable spec during consistency check %s: %s", p, exc
            )
    for err in idea_consistency_errors(loaded):
        LOG.warning("idea-consistency: %s", err)

    results: list[dict] = []
    n_run = 0
    for path in specs:
        if limit is not None and n_run >= limit:
            break
        try:
            spec = yaml.safe_load(path.read_text())
        except Exception as ex:  # noqa: BLE001
            results.append(
                {
                    "spec_file": str(path),
                    "status": "error",
                    "error": f"unreadable yaml: {ex}",
                }
            )
            continue
        scan_name = path.stem
        r = process_spec(
            spec,
            start,
            end,
            scan_name=scan_name,
            actor_id=actor_id,
            split=split,
            results_root=results_root,
            dry_run=dry_run,
            ledger_path=ledger_path,
        )
        r["spec_file"] = str(path)
        if r["status"] not in ("skipped-duplicate", "dry-run"):
            n_run += 1
        results.append(r)
        LOG.info(
            "[%s] %s — %s",
            r["status"],
            scan_name,
            r.get("headline", r.get("error", "")),
        )
        if on_result:
            on_result(r)

    def _count(status):
        return sum(1 for r in results if r["status"] == status)

    return {
        "queue_dir": str(queue_dir),
        "window": [start, end],
        "n_specs": len(specs),
        "counts": {
            s: _count(s)
            for s in (
                "scan-passed",
                "scan-failed",
                "invalid-spec",
                "skipped-duplicate",
                "dry-run",
                "error",
            )
        },
        "passed": [r for r in results if r["status"] == "scan-passed"],
        "results": results,
    }
