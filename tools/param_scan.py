#!/usr/bin/env python3
"""Parametric precision-style scanner.

Signal-level analogue of Himalaya's param_grid: sweeps a formula template
over a parameter grid, computes IC / hit_rate / avg_net_return per combo,
and outputs a heatmap report. Only horizons viable at each observation's
JST bucket (from horizon_lookup.py) are included in each cohort — horizons
that cannot clear the spread are excluded before the metric computation,
not discovered as failures after.

Style: PRECISION only. For skew-style signals use flip_scan.py.

## Formula template
The feature SQL expression is a Python format-string over param names.
Example:
  --feature-template "imbalance_level{depth}"
  --params "depth=1,2,3,5"

The rendered expression is evaluated against the specified Makalu dataset
using DuckDB, ASOF-joined to the target market's forward mid-price return.

## Output
JSON report written to --out (or stdout). Top-level keys:
  signal_style     "precision"
  exchange / symbol / target_exchange / target_symbol
  start_date / end_date / costs_bps
  candidate_horizons        list of tested horizons
  horizon_viability         {H: pct_buckets_viable} for the target market
  grid                      flat list of combo results (one entry per param combo)
  heatmap_axes              which params are the primary and secondary axes
  recommended_combos        combos that cleared all thresholds

Each combo in grid:
  params        dict of param values for this combo
  rendered_feature  the actual SQL expression after template substitution
  per_horizon   list of {horizon_seconds, n_total, n_viable, IC, hit_rate,
                         avg_gross_bps, avg_net_bps, p_viable} — per viable horizon
  best_horizon  horizon with highest IC (among viable), or null
  passed        bool — cleared IC_threshold on >= 1 viable horizon

## Neighborhood rule (anti-overfitting)
A combo that passes but all its single-param-step neighbors fail is flagged
as isolated=True in the output. Isolated winners should be treated as noise.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

_ROOT = Path(__file__).parent.parent
_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
sys.path.insert(0, str(_ROOT / "tools"))
from horizon_lookup import (  # noqa: E402
    annotate_timestamps,
    horizon_viability_summary,
    load_lookup,
)

# Pass thresholds (v1 defaults from evaluation-framework.md)
IC_THRESHOLD = 0.02        # rank IC
HIT_RATE_NET_THRESHOLD = 0.50
AVG_NET_BPS_THRESHOLD = 0.0
PCT_VIABLE_THRESHOLD = 0.25  # horizon must be viable in >= 25% of buckets to be tested


def _ob_glob(exchange: str, symbol: str, data_root: Path) -> str:
    return str(
        data_root / "bronze" / "dataset=OrderBook"
        / f"exchange={exchange}" / f"symbol={symbol}"
        / "date=*" / "*.parquet"
    )


def _bookstate_glob(exchange: str, symbol: str, data_root: Path) -> str:
    return str(
        data_root / "bronze" / "dataset=BookState"
        / f"exchange={exchange}" / f"symbol={symbol}"
        / "date=*" / "*.parquet"
    )


def _compute_combo(
    rendered_feature: str,
    feature_exchange: str,
    feature_symbol: str,
    feature_dataset: str,
    target_exchange: str,
    target_symbol: str,
    start_date: str,
    end_date: str,
    viable_horizons: list[int],
    lookup_df: pd.DataFrame | None,
    costs_bps: float,
    data_root: Path,
) -> list[dict]:
    """Compute IC / hit_rate / return for one formula combo, per viable horizon.

    Returns list of per-horizon result dicts.
    """
    con = duckdb.connect()

    if feature_dataset == "BookState":
        feat_glob = _bookstate_glob(feature_exchange, feature_symbol, data_root)
        feat_sql = f"""
            SELECT ts, ({rendered_feature}) AS signal
            FROM read_parquet('{feat_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
                AND ({rendered_feature}) IS NOT NULL
            ORDER BY ts
        """
    elif feature_dataset == "OrderBook":
        feat_glob = _ob_glob(feature_exchange, feature_symbol, data_root)
        feat_sql = f"""
            SELECT ts,
                   (bid0_price + ask0_price) / 2.0 AS mid,
                   (ask0_price - bid0_price) AS spread,
                   ({rendered_feature}) AS signal
            FROM read_parquet('{feat_glob}', hive_partitioning=true)
            WHERE date >= DATE '{start_date}' AND date <= DATE '{end_date}'
                AND ask0_price > bid0_price
                AND ({rendered_feature}) IS NOT NULL
            ORDER BY ts
        """
    else:
        raise ValueError(f"Unsupported feature_dataset: {feature_dataset}")

    feature_df = con.sql(feat_sql).df()
    if feature_df.empty:
        return []

    target_glob = _ob_glob(target_exchange, target_symbol, data_root)

    # Annotate each observation with viable-horizon flags from lookup
    if lookup_df is not None:
        viability = annotate_timestamps(feature_df["ts"], lookup_df, viable_horizons, k=1)
    else:
        # No lookup: treat all horizons as viable everywhere
        viability = pd.DataFrame({"ts": feature_df["ts"].values})
        for h in viable_horizons:
            viability[f"viable_{h}s"] = True

    feature_df = feature_df.merge(viability[["ts"] + [f"viable_{h}s" for h in viable_horizons]],
                                  on="ts", how="left")

    results = []
    for h in viable_horizons:
        viable_col = f"viable_{h}s"
        viable_mask = feature_df[viable_col].fillna(False)
        n_total = len(feature_df)
        n_viable = int(viable_mask.sum())

        if n_viable < 30:
            results.append({
                "horizon_seconds": h,
                "n_total": n_total,
                "n_viable": n_viable,
                "IC": None,
                "hit_rate": None,
                "hit_rate_net": None,
                "avg_gross_bps": None,
                "avg_net_bps": None,
                "passed": False,
                "skip_reason": "insufficient_viable_observations",
            })
            continue

        viable_df = feature_df[viable_mask].copy()

        # ASOF JOIN: target mid at ts + H
        con.register("_viable_feature", viable_df[["ts", "signal"]])
        joined = con.sql(f"""
            WITH target AS (
                SELECT ts,
                       (bid0_price + ask0_price) / 2.0 AS mid_fwd
                FROM read_parquet('{target_glob}', hive_partitioning=true)
                WHERE date >= DATE '{start_date}'
                  AND date <= DATE '{end_date}'
                  AND ask0_price > bid0_price
            ),
            feat AS (
                SELECT ts, signal FROM _viable_feature
            )
            SELECT f.ts, f.signal,
                   (t.mid_fwd - m.mid_now) / m.mid_now * 10000 AS return_bps
            FROM feat f
            ASOF JOIN target t ON t.ts >= f.ts + {h}
            JOIN (
                SELECT ts,
                       (bid0_price + ask0_price) / 2.0 AS mid_now
                FROM read_parquet('{target_glob}', hive_partitioning=true)
                WHERE date >= DATE '{start_date}'
                  AND date <= DATE '{end_date}'
                  AND ask0_price > bid0_price
            ) m ON m.ts = (
                SELECT max(ts2) FROM (
                    SELECT ts AS ts2 FROM read_parquet('{target_glob}', hive_partitioning=true)
                    WHERE date >= DATE '{start_date}'
                      AND date <= DATE '{end_date}'
                      AND ask0_price > bid0_price
                      AND ts <= f.ts
                ) sub
            )
        """).df().dropna()

        if len(joined) < 30:
            results.append({
                "horizon_seconds": h,
                "n_total": n_total,
                "n_viable": n_viable,
                "IC": None,
                "hit_rate": None,
                "hit_rate_net": None,
                "avg_gross_bps": None,
                "avg_net_bps": None,
                "passed": False,
                "skip_reason": "insufficient_joined_observations",
            })
            continue

        ret = joined["return_bps"].values
        sig = joined["signal"].values

        ic, _ = spearmanr(sig, ret)
        hit_rate = float((np.sign(sig) == np.sign(ret)).mean())
        hit_rate_net = float(((np.sign(sig) * ret) > costs_bps).mean())
        avg_gross = float((np.sign(sig) * ret).mean())
        avg_net = avg_gross - costs_bps

        passed = (
            abs(ic) >= IC_THRESHOLD
            and hit_rate_net >= HIT_RATE_NET_THRESHOLD
            and avg_net >= AVG_NET_BPS_THRESHOLD
        )

        results.append({
            "horizon_seconds": h,
            "n_total": n_total,
            "n_viable": n_viable,
            "IC": round(float(ic), 4),
            "hit_rate": round(hit_rate, 4),
            "hit_rate_net": round(hit_rate_net, 4),
            "avg_gross_bps": round(avg_gross, 3),
            "avg_net_bps": round(avg_net, 3),
            "passed": passed,
        })

    return results


def run_param_scan(
    feature_template: str,
    params: dict[str, list],
    feature_exchange: str,
    feature_symbol: str,
    feature_dataset: str,
    target_exchange: str,
    target_symbol: str,
    start_date: str,
    end_date: str,
    candidate_horizons: list[int],
    costs_bps: float = 1.0,
    data_root: Path = _DEFAULT_DATA_ROOT,
) -> dict:
    """Run parametric scan. Returns heatmap report dict."""

    # Load horizon lookup and filter candidate horizons by viability
    lookup_df = load_lookup(target_exchange, target_symbol)
    if lookup_df is not None:
        viability = horizon_viability_summary(lookup_df, candidate_horizons, k=1)
        viable_horizons = [h for h, pct in viability.items() if pct >= PCT_VIABLE_THRESHOLD]
        skipped_horizons = [h for h, pct in viability.items() if pct < PCT_VIABLE_THRESHOLD]
    else:
        viability = {h: None for h in candidate_horizons}
        viable_horizons = candidate_horizons
        skipped_horizons = []

    print(f"Target: {target_exchange}/{target_symbol}")
    print(f"Viable horizons (>={PCT_VIABLE_THRESHOLD:.0%} of buckets): {viable_horizons}")
    if skipped_horizons:
        print(f"Skipped horizons (below threshold): {skipped_horizons}")

    # Build all param combos
    param_names = list(params.keys())
    param_values = list(params.values())
    combos = list(itertools.product(*param_values))

    print(f"Param grid: {len(combos)} combos × {len(viable_horizons)} horizons")

    grid = []
    for combo in combos:
        param_dict = dict(zip(param_names, combo))
        rendered = feature_template.format(**param_dict)
        print(f"  {param_dict}  feature={rendered} ...", end=" ", flush=True)

        per_horizon = _compute_combo(
            rendered_feature=rendered,
            feature_exchange=feature_exchange,
            feature_symbol=feature_symbol,
            feature_dataset=feature_dataset,
            target_exchange=target_exchange,
            target_symbol=target_symbol,
            start_date=start_date,
            end_date=end_date,
            viable_horizons=viable_horizons,
            lookup_df=lookup_df,
            costs_bps=costs_bps,
            data_root=data_root,
        )

        best = max(
            (r for r in per_horizon if r.get("IC") is not None),
            key=lambda r: abs(r["IC"]),
            default=None,
        )
        passed = any(r.get("passed") for r in per_horizon)

        print(f"IC_best={best['IC'] if best else 'n/a'}  passed={passed}")

        grid.append({
            "params": param_dict,
            "rendered_feature": rendered,
            "per_horizon": per_horizon,
            "best_horizon": best["horizon_seconds"] if best else None,
            "best_IC": best["IC"] if best else None,
            "passed": passed,
        })

    # Neighborhood rule: flag isolated winners
    passing_param_sets = {
        tuple(sorted(g["params"].items()))
        for g in grid if g["passed"]
    }
    for g in grid:
        if not g["passed"]:
            g["isolated"] = False
            continue
        this_params = g["params"]
        has_passing_neighbor = False
        for pname, pval in this_params.items():
            all_vals = sorted(params[pname])
            idx = all_vals.index(pval)
            for neighbor_idx in [idx - 1, idx + 1]:
                if 0 <= neighbor_idx < len(all_vals):
                    neighbor = dict(this_params)
                    neighbor[pname] = all_vals[neighbor_idx]
                    if tuple(sorted(neighbor.items())) in passing_param_sets:
                        has_passing_neighbor = True
                        break
            if has_passing_neighbor:
                break
        g["isolated"] = not has_passing_neighbor

    recommended = [
        g for g in grid if g["passed"] and not g["isolated"]
    ]

    return {
        "signal_style": "precision",
        "feature_template": feature_template,
        "feature_exchange": feature_exchange,
        "feature_symbol": feature_symbol,
        "feature_dataset": feature_dataset,
        "target_exchange": target_exchange,
        "target_symbol": target_symbol,
        "start_date": start_date,
        "end_date": end_date,
        "costs_bps": costs_bps,
        "candidate_horizons": candidate_horizons,
        "horizon_viability": {str(k): v for k, v in viability.items()},
        "viable_horizons_used": viable_horizons,
        "skipped_horizons": skipped_horizons,
        "param_grid": params,
        "n_combos": len(combos),
        "grid": grid,
        "heatmap_axes": param_names[:2] if len(param_names) >= 2 else param_names,
        "recommended_combos": [
            {"params": g["params"], "best_IC": g["best_IC"], "best_horizon": g["best_horizon"]}
            for g in recommended
        ],
        "thresholds": {
            "IC": IC_THRESHOLD,
            "hit_rate_net": HIT_RATE_NET_THRESHOLD,
            "avg_net_bps": AVG_NET_BPS_THRESHOLD,
            "pct_viable_buckets": PCT_VIABLE_THRESHOLD,
        },
    }


def print_heatmap(report: dict) -> None:
    """Print a text heatmap of IC by the first two param axes."""
    grid = report["grid"]
    axes = report["heatmap_axes"]
    if len(axes) < 2:
        for g in grid:
            best = g.get("best_IC")
            flag = " *PASS*" if g["passed"] and not g["isolated"] else (" (isolated)" if g["passed"] else "")
            print(f"  {g['params']}  IC={best}{flag}")
        return

    ax0, ax1 = axes[0], axes[1]
    ax0_vals = sorted(set(g["params"][ax0] for g in grid))
    ax1_vals = sorted(set(g["params"][ax1] for g in grid))

    ic_map: dict[tuple, float | None] = {}
    flag_map: dict[tuple, str] = {}
    for g in grid:
        key = (g["params"][ax0], g["params"][ax1])
        ic_map[key] = g.get("best_IC")
        flag_map[key] = "**" if (g["passed"] and not g["isolated"]) else ("!!" if g.get("isolated") else "  ")

    col_w = 10
    print(f"\n  IC heatmap ({ax0} × {ax1})  ** = pass  !! = isolated pass")
    header = f"{'':>12}" + "".join(f"{str(v):>{col_w}}" for v in ax1_vals)
    print(header)
    for v0 in ax0_vals:
        row = f"{str(v0):>12}"
        for v1 in ax1_vals:
            ic = ic_map.get((v0, v1))
            flag = flag_map.get((v0, v1), "  ")
            cell = f"{ic:+.3f}" if ic is not None else "  none"
            row += f"  {flag}{cell:>7}"
        print(row)

    recs = report["recommended_combos"]
    if recs:
        print(f"\n  Recommended combos ({len(recs)}):")
        for r in recs:
            print(f"    {r['params']}  IC={r['best_IC']}  H={r['best_horizon']}s")
    else:
        print("\n  No combos cleared all thresholds (or all passers are isolated).")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parametric precision-style scanner — param grid → IC heatmap"
    )
    parser.add_argument("--feature-template", required=True,
                        help="Python format-string for the SQL feature expression. "
                             "Use {param_name} placeholders. "
                             "Example: 'imbalance_level{depth}'")
    parser.add_argument("--params", required=True,
                        help="param_name=v1,v2,...[;param2=...]. "
                             "Example: 'depth=1,2,3,5;horizon=30,120,300'")
    parser.add_argument("--feature-exchange", required=True)
    parser.add_argument("--feature-symbol", required=True)
    parser.add_argument("--feature-dataset", default="BookState",
                        choices=["BookState", "OrderBook"],
                        help="Source dataset for the feature expression")
    parser.add_argument("--target-exchange", required=True)
    parser.add_argument("--target-symbol", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--horizons", default="15,30,60,120,300,600",
                        help="Candidate horizons in seconds (filtered by viability)")
    parser.add_argument("--costs-bps", type=float, default=1.0)
    parser.add_argument("--out", default=None, help="Write JSON report to this path")
    parser.add_argument("--data-root", default="/panda/makalu-data")
    args = parser.parse_args()

    # Parse params
    param_dict: dict[str, list] = {}
    for part in args.params.split(";"):
        name, vals_str = part.split("=", 1)
        vals = [v.strip() for v in vals_str.split(",")]
        # Infer numeric type
        try:
            param_dict[name.strip()] = [int(v) for v in vals]
        except ValueError:
            try:
                param_dict[name.strip()] = [float(v) for v in vals]
            except ValueError:
                param_dict[name.strip()] = vals

    candidate_horizons = [int(h) for h in args.horizons.split(",")]

    report = run_param_scan(
        feature_template=args.feature_template,
        params=param_dict,
        feature_exchange=args.feature_exchange,
        feature_symbol=args.feature_symbol,
        feature_dataset=args.feature_dataset,
        target_exchange=args.target_exchange,
        target_symbol=args.target_symbol,
        start_date=args.start,
        end_date=args.end,
        candidate_horizons=candidate_horizons,
        costs_bps=args.costs_bps,
        data_root=Path(args.data_root),
    )

    print_heatmap(report)

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
        print(f"\nWrote {args.out}")
    else:
        print("\n(Use --out FILE to save the full JSON report)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
