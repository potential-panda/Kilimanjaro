"""`cli.py meta` — LGBM meta-labeling on top of the linear composite.

Takes the same factor specs as `blend`, fits the IC-weighted linear composite
(the primary signal), fires it LOOSELY (default z>=1.5) to get a big candidate
pool, labels each non-overlapping trade by triple-barrier net return, and trains
a heavily-regularized LGBM to predict *which fired trades are profitable*
(meta-labeling). It never picks direction — the linear composite does that.

The verdict is a RANKING comparison on holdout: does LGBM's P(profitable)
ordering beat the incumbent |composite z| ordering at matched trade counts? If
yes, interaction alpha beyond the linear blend exists; if not, |z| is already the
best selector (a clean negative result). See driver/meta_label.py for the design.

    python src/cli.py meta --start 2025-07-01 --end 2026-06-30 \
        --primary-threshold 1.5 --barrier-bps 10 --time-cap 300 \
        --jst-hours 22,23,0,1,2 \
        $(ls cycle/12_specs/*BTC_JPY.yaml)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from kilimanjaro.framework import MarketConfig, default_split, split_windows, write_csv

_RESULTS = Path("cycle/15_return_eval/meta")


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("meta", help="LGBM meta-labeling on the linear composite")
    p.add_argument("specs", nargs="+", help="Scan spec YAMLs (must share one target)")
    p.add_argument("--start", required=True, help="Window start YYYY-MM-DD")
    p.add_argument("--end", required=True, help="Window end YYYY-MM-DD")
    p.add_argument(
        "--split",
        default=None,
        help="Train/holdout split date (default: 2/3 of the range)",
    )
    p.add_argument(
        "--horizon",
        type=int,
        default=300,
        help="Blend IC horizon + triple-barrier time cap fallback (s)",
    )
    p.add_argument(
        "--primary-threshold",
        type=float,
        default=1.5,
        help="Loose primary |z| threshold defining the candidate pool",
    )
    p.add_argument(
        "--barrier-bps",
        type=float,
        default=10.0,
        help="Triple-barrier symmetric take-profit/stop (bps)",
    )
    p.add_argument(
        "--time-cap",
        type=int,
        default=300,
        help="Triple-barrier vertical barrier / non-overlap spacing (s)",
    )
    p.add_argument(
        "--objective",
        default="classify",
        choices=["classify", "regress"],
        help="classify (v1: P(net>0), optimizes hit count) or "
        "regress (v2: predict net_bps, magnitude-aware)",
    )
    p.add_argument(
        "--min-weight",
        type=float,
        default=0.01,
        help="Drop factors with |train IC| below this from the composite",
    )
    p.add_argument(
        "--jst-hours",
        default=None,
        help="Restrict candidates to these JST hours (e.g. 22,23,0,1,2)",
    )
    p.add_argument(
        "--retarget",
        default=None,
        metavar="EX/SYM/SIZE",
        help="Execute on a different venue than the specs' target "
        "(e.g. COINCHECK/BTC_SPOT/0.002); keeps the composite + "
        "its weights, swaps only the execution/basis leg",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Output dir (default under cycle/15_return_eval/meta/)",
    )
    p.set_defaults(func=_run_meta)


def _run_meta(args: argparse.Namespace) -> int:
    from kilimanjaro.driver.blend_corr import blend, composite_spec, factors_from_specs
    from kilimanjaro.driver.meta_label import (
        build_candidate_table,
        compare_rankings,
        feature_cols_for,
        fit_meta,
        natural_operating_point,
    )

    specs = [yaml.safe_load(Path(s).read_text()) for s in args.specs]
    targets = {(s["target"]["exchange"], s["target"]["symbol"]) for s in specs}
    if len(targets) != 1:
        print(f"specs must share one target, got: {sorted(targets)}")
        return 2
    t = specs[0]["target"]
    target = MarketConfig(t["exchange"], t["symbol"], t["execution_size"])
    split = args.split or default_split(args.start, args.end)
    jst_hours = {int(h) for h in args.jst_hours.split(",")} if args.jst_hours else None

    # 1. Fit the linear composite (primary signal) on train.
    factors = factors_from_specs(specs)
    res = blend(factors, target, args.start, split, args.end, args.horizon)
    weights = {p["factor"]: p["weight"] for p in res["per_factor"]}
    comp = composite_spec(
        specs,
        factors,
        weights,
        thresholds=[args.primary_threshold],
        horizon=args.horizon,
        min_abs_weight=args.min_weight,
        return_calc="triple_barrier",
        barrier_bps=args.barrier_bps,
        time_cap=args.time_cap,
    )
    composite_expr = comp["expressions"][0]["expr"]
    markets = dict(comp["markets"])

    # Optional retarget: keep the composite + its (spec-target-fit) weights, but
    # EXECUTE on a different venue — rewrite the execution/basis leg (the alias
    # matching the spec target) and the target. Factor features are all on the
    # `a` leader, so only `b_mid` changes. Mirrors composite_coincheck.yaml.
    if args.retarget:
        rex, rsym, rsize = args.retarget.split("/")
        old = (t["exchange"], t["symbol"])
        for alias, m in markets.items():
            if (m["exchange"], m["symbol"]) == old:
                markets[alias] = {
                    "exchange": rex,
                    "symbol": rsym,
                    "execution_size": rsize,
                }
        target = MarketConfig(rex, rsym, rsize)

    hours_note = f" | JST hours {sorted(jst_hours)}" if jst_hours else ""
    print(
        f"meta: {len(factors)} factors -> {target.exchange}/{target.symbol} | "
        f"primary |z|>={args.primary_threshold} | barrier {args.barrier_bps}bps "
        f"cap {args.time_cap}s{hours_note}"
        + (f" | weights fit on {t['exchange']}/{t['symbol']}" if args.retarget else "")
    )

    # 2. Candidate tables on disjoint train / holdout windows.
    tr_s, tr_e, ho_s, ho_e = split_windows(args.start, split, args.end)
    train_tbl = build_candidate_table(
        composite_expr,
        factors,
        markets,
        target,
        args.primary_threshold,
        tr_s,
        tr_e,
        barrier_bps=args.barrier_bps,
        time_cap=args.time_cap,
        jst_hours=jst_hours,
    )
    holdout_tbl = build_candidate_table(
        composite_expr,
        factors,
        markets,
        target,
        args.primary_threshold,
        ho_s,
        ho_e,
        barrier_bps=args.barrier_bps,
        time_cap=args.time_cap,
        jst_hours=jst_hours,
    )
    if train_tbl.empty or holdout_tbl.empty:
        print(
            f"  not enough candidates (train={len(train_tbl)}, "
            f"holdout={len(holdout_tbl)}) — loosen threshold or widen window"
        )
        return 1
    print(
        f"  candidates: train n={len(train_tbl)} "
        f"(win rate {train_tbl['label'].mean():.3f}), "
        f"holdout n={len(holdout_tbl)} (win rate {holdout_tbl['label'].mean():.3f})"
    )

    # 3. Fit + score.
    feature_cols = feature_cols_for(factors)
    fit = fit_meta(train_tbl, holdout_tbl, feature_cols, objective=args.objective)
    if args.objective == "classify":
        print(
            f"  LGBM classify: best_iter={fit['best_iteration']} "
            f"val_auc={fit['val_auc']:.4f} holdout_auc={fit['holdout_auc']:.4f}  "
            f"(0.5 = no separation)"
        )
        nat_cut = 0.5
    else:
        print(
            f"  LGBM regress: best_iter={fit['best_iteration']} "
            f"val_rank_ic={fit['val_rank_ic']:.4f} "
            f"holdout_rank_ic={fit['holdout_rank_ic']:.4f} "
            f"holdout_ic={fit['holdout_ic']:.4f}  (0 = no ranking skill)"
        )
        nat_cut = 0.0

    # 4. The verdict: meta ranking vs |z| ranking at matched trade counts.
    rows = compare_rankings(holdout_tbl, fit["preds"])
    print("\n  holdout: meta P(profit) ranking  vs  incumbent |z| ranking")
    print(
        f"  {'frac':>5} {'n':>6} | {'z_avg':>7} {'z_total':>9} {'z_hit':>6} | "
        f"{'meta_avg':>8} {'meta_total':>10} {'meta_hit':>8} | {'win?':>4}"
    )
    for r in rows:
        win = "META" if r["meta_total_net"] > r["z_total_net"] else "z"
        print(
            f"  {r['frac']:>5.2f} {r['n']:>6} | "
            f"{r['z_avg_net']:>7.3f} {r['z_total_net']:>9.1f} {r['z_hit']:>6.3f} | "
            f"{r['meta_avg_net']:>8.3f} {r['meta_total_net']:>10.1f} "
            f"{r['meta_hit']:>8.3f} | {win:>4}"
        )

    nop = natural_operating_point(holdout_tbl, fit["preds"], cut=nat_cut)
    cut_label = "P>=0.5" if args.objective == "classify" else "pred_net>=0"
    print(
        f"\n  meta natural cut ({cut_label}): takes {nop['meta_n']}/{nop['pool_n']} "
        f"trades, avg_net {nop['meta_avg_net']:.3f} (pool avg {nop['pool_avg_net']:.3f}), "
        f"total {nop['meta_total_net']:.1f} (pool {nop['pool_total_net']:.1f})"
    )

    print("\n  top features by gain:")
    for name, imp in fit["importances"][:10]:
        human = _human(name, factors)
        print(f"    {name:>8} {imp:>7}  {human}")

    # 5. Persist.
    out_dir = (
        Path(args.out)
        if args.out
        else (
            _RESULTS
            / f"{target.symbol}_{args.start}_{args.end}_z{args.primary_threshold}_{args.objective}"
        )
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(rows, out_dir / "ranking_compare.csv", list(rows[0].keys()))
    write_csv(
        [
            {"feature": n, "human": _human(n, factors), "gain": int(g)}
            for n, g in fit["importances"]
        ],
        out_dir / "importances.csv",
        ["feature", "human", "gain"],
    )
    holdout_tbl.assign(pred=fit["preds"]).to_parquet(out_dir / "holdout_scored.parquet")
    print(f"\n  -> {out_dir}")
    print(
        "  NB: expression+triple_barrier is uncalibrated (trend-only) — this is "
        "a research diagnostic on the SCAN, not a promotion number."
    )
    return 0


def _human(col: str, factors) -> str:
    from kilimanjaro.driver.meta_label import factor_columns

    return factor_columns(factors).get(col, col)
