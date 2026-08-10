"""cli.py blend-corr — stage 14: build + rank weighted blends of the stage-13 factors.

Consumes the factor families in cycle/12_specs, constructs several candidate blends
(single_best / ic_weighted / ic_decorr / greedy — all fit on TRAIN), and ranks them by
honest HOLDOUT IC. A strong single factor competes as a blend-of-one. Writes, per venue,
cycle/14_blend_corr/<venue>/{blends.csv, per_factor.csv, top_blend.yaml} — the top blend's
spec is the hand-off to stage 15 (return_eval). See doc/research-pipeline.md.

    python src/cli.py blend-corr --families BTC \
        --venues GMO:BTC_JPY:0.01,COINCHECK:BTC_SPOT:0.002 \
        --start 2025-08-01 --end 2026-06-30 --horizon 30
"""

from __future__ import annotations

import argparse
import glob
from pathlib import Path

import yaml

from kilimanjaro.driver.blend_corr import (
    Factor,
    _freeze_markets,
    composite_spec,
    scan_blends,
)
from kilimanjaro.framework import MarketConfig, default_split

_RESULTS = Path("cycle/14_blend_corr")
_LEADER = {"exchange": "BINANCE", "symbol": "BTC_USDT", "execution_size": "0.002"}


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("blend-corr",
                       help="Stage 14: construct + rank weighted blends by holdout IC")
    p.add_argument("--families", required=True, help="base symbol (e.g. BTC) — scan its 12_specs families")
    p.add_argument("--venues", default="GMO:BTC_JPY:0.01,COINCHECK:BTC_SPOT:0.002",
                   help="execution venues EXCH:SYM:SIZE,...")
    p.add_argument("--leader", default="BINANCE:BTC_USDT:0.002")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--split", default=None, help="train/holdout split date (default: last third)")
    p.add_argument("--horizon", type=int, default=30, help="forward-return horizon seconds (default 30)")
    p.add_argument("--ridge", type=float, default=0.2, help="decorr ridge (default 0.2)")
    p.add_argument("--lgbm", action="store_true",
                   help="also fit an LGBM capacity ceiling (nonlinear headroom vs the linear blend)")
    p.set_defaults(func=_run)


def _mkt(s: str) -> dict:
    e, sym, size = s.split(":")
    return {"exchange": e, "symbol": sym, "execution_size": size}


def _venue_factors(base_sym: str, leader: dict, venue: dict) -> list[Factor]:
    """Distinct 12_specs factors for base_sym, rebuilt on (a=leader, b=venue) markets."""
    mk = _freeze_markets({"a": leader, "b": venue})
    seen, out = set(), []
    for fp in sorted(glob.glob("cycle/12_specs/*.yaml")):
        spec = yaml.safe_load(Path(fp).read_text())
        if spec["target"]["symbol"].split("_")[0] != base_sym:
            continue
        for e in spec["expressions"]:
            expr = e["expr"]
            if expr in seen:
                continue
            seen.add(expr)
            out.append(Factor(name=e.get("label") or expr, markets=mk, expr=expr))
    return out


def _run(args) -> int:
    leader = _mkt(args.leader)
    split = args.split or default_split(args.start, args.end)
    for vs in args.venues.split(","):
        venue = _mkt(vs)
        factors = _venue_factors(args.families, leader, venue)
        target = MarketConfig(venue["exchange"], venue["symbol"], venue["execution_size"])
        print(f"\n===== blend-corr {venue['exchange']}:{venue['symbol']} — "
              f"{len(factors)} factors | h={args.horizon}s | train {args.start}..{split} "
              f"holdout {split}..{args.end} =====")
        res = scan_blends(factors, target, args.start, split, args.end,
                          args.horizon, ridge=args.ridge, use_lgbm=args.lgbm)
        if not res["results"]:
            print("  (no overlapping panel — skipped)")
            continue

        print(f"  best single-factor holdout IC: {res['best_single_holdout_ic']:+.4f}  "
              f"(train n={res['train_n']}, holdout n={res['holdout_n']})")
        print(f"  {'method':<13} {'nFac':>4} {'trainIC':>8} {'holdoutIC':>10} {'div×':>6}")
        for r in res["results"]:
            tag = "  ← nonlinear ceiling (not a formula)" if r["method"] == "lgbm" else ""
            print(f"  {r['method']:<13} {r['n_factors']:>4} {r['train_ic']:>+8.4f} "
                  f"{r['holdout_ic']:>+10.4f} {r['div_ratio']:>6}{tag}")
        if res.get("lgbm_importances"):
            top = sorted(res["lgbm_importances"].items(), key=lambda kv: -kv[1])[:6]
            print("  LGBM top feature importances: "
                  + ", ".join(f"{n} {w:.2f}" for n, w in top))

        import pandas as pd
        out = _RESULTS / f"{args.families}_{vs.replace(':', '_')}"
        out.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{k: v for k, v in r.items() if k != "weights"} for r in res["results"]]
                     ).to_csv(out / "blends.csv", index=False)
        pd.DataFrame(res["per_factor"]).to_csv(out / "per_factor.csv", index=False)

        # emit the top LINEAR blend as a stage-15 spec (LGBM is a model, not a formula)
        linear = [r for r in res["results"] if r["method"] != "lgbm"]
        top = linear[0]
        wf = [f for f in factors if f.name in top["weights"]] or factors
        base = {"markets": wf[0].markets_dict,
                "target": {"exchange": venue["exchange"], "symbol": venue["symbol"],
                           "execution_size": venue["execution_size"]}}
        spec = composite_spec(
            [base], wf, top["weights"], thresholds=[2.0, 3.0, 3.5], horizon=args.horizon,
            return_calc="trailing_stop", barrier_bps=10.0)
        spec["hypothesis_id"] = f"blend-{args.families.lower()}-{venue['symbol'].lower()}"
        (out / "top_blend.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
        print(f"  top blend: {top['method']} ({top['n_factors']} factors, "
              f"holdout IC {top['holdout_ic']:+.4f}) → {out}/top_blend.yaml")
    return 0
