"""`cli.py blend` — the linear IC-weighted combination baseline (step 3).

Takes several scan specs, collects their distinct continuous factors, fits an
IC-weighted blend on train, and reports the blended holdout IC against the best
single factor — the floor a fancier (LGBM) blend must beat. All specs must share
one target; the factors may span different markets (cross-market prediction).

    python src/cli.py blend --horizon 120 --start 2025-07-01 --end 2026-06-30 \
        cycle/12_specs/hyp-20260722-001_momentum_ETH_JPY.yaml \
        cycle/12_specs/hyp-20260722-002_obi_ETH_JPY.yaml \
        cycle/12_specs/hyp-20260722-003_reversion_ETH_JPY.yaml \
        cycle/12_specs/hyp-20260714-002_basis_ETH_JPY.yaml

This measures predictive power only (mid-to-mid, no cost, non-overlapping). It
does NOT run the step-2 realistic sim — that is a separate call on the winning
blend.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from kilimanjaro.framework import MarketConfig, default_split, write_csv

_RESULTS = Path("cycle/13_results/blend")


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("blend", help="Linear IC-weighted combination baseline (step 3)")
    p.add_argument("specs", nargs="+", help="Scan spec YAMLs (must share one target)")
    p.add_argument("--start", required=True, help="Window start YYYY-MM-DD")
    p.add_argument("--end", required=True, help="Window end YYYY-MM-DD")
    p.add_argument("--split", default=None, help="Train/holdout split date "
                   "(default: 2/3 of the range)")
    p.add_argument("--horizon", type=int, default=120, help="Forward horizon seconds")
    p.add_argument("--step2", action="store_true",
                   help="After the blend, run the composite through the realistic "
                        "episode sim (bid/ask, gate, cost) and report net PnL")
    p.add_argument("--min-weight", type=float, default=0.01,
                   help="Drop factors with |train IC| below this from the composite")
    p.add_argument("--out", default=None, help="Output dir (default under "
                   "cycle/13_results/blend/)")
    p.set_defaults(func=_run_blend)


def _run_blend(args: argparse.Namespace) -> int:
    from kilimanjaro.driver.blend import blend, factors_from_specs

    specs = [yaml.safe_load(Path(s).read_text()) for s in args.specs]
    targets = {(s["target"]["exchange"], s["target"]["symbol"]) for s in specs}
    if len(targets) != 1:
        print(f"specs must share one target, got: {sorted(targets)}")
        return 2
    t = specs[0]["target"]
    target = MarketConfig(t["exchange"], t["symbol"], t["execution_size"])
    split = args.split or default_split(args.start, args.end)

    factors = factors_from_specs(specs)
    print(f"blend: {len(factors)} distinct factors -> {t['exchange']}/{t['symbol']} "
          f"| horizon={args.horizon}s | train<{split}<=holdout")

    res = blend(factors, target, args.start, split, args.end, args.horizon)

    print(f"\n  train n={res['train_n']}  holdout n={res['holdout_n']}")
    print(f"  {'factor':<32} {'weight':>9} {'train_ic':>9} {'holdout_ic':>11}")
    for p in res["per_factor"]:
        print(f"  {p['factor']:<32} {p['weight']:>9.4f} {p['train_ic']:>9.4f} "
              f"{p['holdout_ic']:>11.4f}")
    print(f"\n  BLEND      train_ic={res['blend_train_ic']:+.4f}  "
          f"holdout_ic={res['blend_holdout_ic']:+.4f}")
    print(f"  best single-factor holdout |ic| = {res['best_single_holdout_ic']:.4f}")
    dr = res["diversification_ratio"]
    print(f"  diversification ratio (blend/best) = "
          f"{dr:.2f}" + ("  (>1 = combination helped)" if dr == dr else ""))

    out_dir = Path(args.out) if args.out else (
        _RESULTS / f"{t['symbol']}_{args.start}_{args.end}_h{args.horizon}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(res["per_factor"], out_dir / "per_factor.csv",
              ["factor", "weight", "train_ic", "holdout_ic"])
    summary = {k: v for k, v in res.items() if k != "per_factor"}
    write_csv([summary], out_dir / "summary.csv", list(summary.keys()))
    print(f"\n  -> {out_dir}")

    if args.step2:
        _run_step2(specs, factors, res, target, split, args, out_dir)
    return 0


def _run_step2(specs, factors, res, target, split, args, out_dir) -> None:
    """Run the IC-weighted composite through the realistic episode sim."""
    from kilimanjaro.driver.blend import composite_spec
    from kilimanjaro.driver.scan_runner import run_spec

    weights = {p["factor"]: p["weight"] for p in res["per_factor"]}
    comp = composite_spec(
        specs, factors, weights,
        thresholds=[1.5, 2.5], horizon=args.horizon,
        min_abs_weight=args.min_weight, return_calc="episode",
    )
    n_terms = comp["expressions"][0]["expr"].count("*(")
    print(f"\n  step-2 sim: composite of {n_terms} factors (|IC| >= {args.min_weight}), "
          f"episode + gate + cost, threshold sweep 1.5/2.5")
    scan_res, s2_dir, _ = run_spec(
        comp, args.start, args.end,
        results_root=out_dir / "step2", split=split, scan_name="blend_step2",
    )
    print(f"  {'thr':>4} {'ho_episodes':>12} {'ho_avg_net':>11} {'hit_net':>8} "
          f"{'ho_total_net':>13} {'sharpe':>7} {'dd_blk':>7}")
    for r in sorted(scan_res["rows"], key=lambda r: r.get("threshold", 0)):
        print(f"  {r.get('threshold'):>4} {r.get('holdout_n_episodes', 0):>12} "
              f"{_f(r.get('holdout_avg_net')):>11} {_f(r.get('holdout_hit_rate_net')):>8} "
              f"{_f(r.get('holdout_total_net_bps')):>13} {_f(r.get('holdout_sharpe_per_trade')):>7} "
              f"{r.get('holdout_n_dd_blocked', 0):>7}")
    print(f"  -> {s2_dir}")
    print("  NB: expression+episode is trend-only (uncalibrated) — direction/"
          "magnitude indicative, not a promotion number (see doc/calibration.md)")


def _f(v) -> str:
    return f"{v:.3f}" if isinstance(v, (int, float)) and v == v else "—"
