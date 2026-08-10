"""cli.py signal-corr — stage 13: per-factor correlation screen.

Measures each factor's IC (signal ↔ forward return, across horizons) and the
cross-factor correlation matrix, from a spec's markets + factor set. Screens the
components of a blend spec (splits `zscore(Σ wᵢ·factorᵢ, W)` into its terms) or an
explicit `factors:` list. Writes cycle/13_signal_corr/<spec>/{ic.csv,corr_matrix.csv}.

    python src/cli.py signal-corr --spec cycle/18_handoffs/blend-btc-jpy-us-z35/composite.yaml \
        --start 2026-03-01 --end 2026-03-31 --horizons 10,30,60,300

See kilimanjaro.driver.signal_corr and doc/research-pipeline.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from kilimanjaro.driver.signal_corr import (
    Factor,
    compute,
    factors_from_blend_expr,
)

_RESULTS = Path("cycle/13_signal_corr")


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("signal-corr",
                       help="Stage 13: per-factor IC + cross-factor correlation matrix")
    p.add_argument("--spec", required=True, help="YAML spec (markets + blend expr or factors list)")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--horizons", default="10,30,60,300",
                   help="forward-return horizons in seconds (default 10,30,60,300)")
    p.add_argument("--target-mid", default="b_mid",
                   help="feature-frame expr for the execution venue mid (default b_mid)")
    p.add_argument("--out", default=None, help="output dir (default cycle/13_signal_corr/<spec>)")
    p.set_defaults(func=_run)


def _factors_from_spec(spec: dict) -> list[Factor]:
    if spec.get("factors"):  # explicit list of {name, expr}
        return [Factor(f["name"], f["expr"]) for f in spec["factors"]]
    return factors_from_blend_expr(spec["expressions"][0]["expr"])  # split the blend


def _run(args) -> int:
    spec = yaml.safe_load(Path(args.spec).read_text())
    factors = _factors_from_spec(spec)
    horizons = [int(h) for h in args.horizons.split(",")]
    print(f"signal-corr: {Path(args.spec).stem} — {len(factors)} factors × "
          f"{len(horizons)} horizons | {args.start}..{args.end}")

    res = compute(spec["markets"], factors, args.target_mid,
                  args.start, args.end, horizons)

    out = Path(args.out) if args.out else _RESULTS / Path(args.spec).stem
    out.mkdir(parents=True, exist_ok=True)
    res.ic.to_csv(out / "ic.csv", index=False)
    res.corr.to_csv(out / "corr_matrix.csv")

    h = max(horizons)
    print(f"\nranked by |rank_ic| at h={h}s (IC = corr of signal vs forward return):")
    print(f"  {'factor':<30} {'rank_ic':>8} {'ic':>8} {'n':>8}")
    for _, r in res.ranked(h).iterrows():
        print(f"  {r.factor:<30} {r.rank_ic:>+8.4f} {r.ic:>+8.4f} {int(r.n):>8}")
    # mean absolute cross-correlation = how much orthogonality the blend has to gain
    import numpy as np
    m = res.corr.to_numpy()
    off = m[~np.eye(len(m), dtype=bool)]
    print(f"\nmean |cross-factor corr|: {np.nanmean(np.abs(off)):.3f}  "
          f"(lower = more orthogonal → more blend headroom)")
    print(f"\nwrote {out}/ic.csv, {out}/corr_matrix.csv")
    return 0
