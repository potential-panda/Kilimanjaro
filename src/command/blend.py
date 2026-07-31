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
    return 0
