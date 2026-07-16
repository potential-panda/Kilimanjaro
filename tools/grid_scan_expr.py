#!/usr/bin/env python3
"""Expression grid scan: sweep signal FORMULAS, not hand-written classes.

Reads a small YAML spec describing markets, expressions, and the sweep
dimensions, builds ExpressionSignal × FixedHorizonReturn combos, and runs
them through run_grid_split (train/holdout + rank stability, same as every
other grid). One `scan_attached` ledger event per run.

Example spec (YAML)
-------------------
    hypothesis_id: hyp-20260713-001          # or pass --hypothesis-id / --no-ledger
    markets:
      a: {exchange: BINANCE, symbol: ETH_USDT, execution_size: "0.1"}
      b: {exchange: GMO,     symbol: ETH_JPY,  execution_size: "0.1"}
    target: {exchange: GMO, symbol: ETH_JPY, execution_size: "0.3"}
    expressions:
      - label: basis_z_1h
        expr: "zscore(a_mid / b_mid, 3600)"
        invert: [true]                        # fade the divergence
      - label: obi_z_10m
        expr: "zscore(a_obi_ema_k5_60s, 600)"
        invert: [false]
    thresholds: [1.5, 2.0, 2.5]
    age_windows: [[null, null], [10, 300]]
    horizons: [30, 60, 120, 300]

Return calc (default `fixed_horizon`, mid-to-mid look-ahead from every active
second). For the non-overlapping, spread-aware measurement (one bid/ask trade
per episode — see doc/overlapping-returns.md), set instead:

    return_calc: episode
    max_holds: [60, 300, null]     # cap hold in seconds; null = to episode end

(`max_holds` replaces `horizons` in episode mode.)

Available ops inside `expr`: see kilimanjaro/op/series.py OPS (ema, zscore,
roll_mean/std/sum/max/min, lag, diff, ret, sign, abs, clip) — all causal.
Frame columns: {alias}_mid, {alias}_spread_bps, {alias}_obi_k{d},
{alias}_obi_ema_k{d}_{hl}s, {alias}_ta_{col}.

Usage
-----
    python tools/grid_scan_expr.py --spec my_expr_grid.yaml --start 2026-05-01 --end 2026-06-30
    python tools/grid_scan_expr.py --list-features BINANCE ETH_USDT
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilimanjaro.framework import (
    MarketConfig,
    ScheduleFilter,
    default_split,
    run_grid_split,
    write_csv,
)
# build_combos + the CSV field list live in the driver's scan_runner, the
# canonical spec -> scan path; this tool is a thin CLI over it.
from kilimanjaro.driver.scan_runner import CSV_FIELDS as _CSV_FIELDS
from kilimanjaro.driver.scan_runner import build_combos


def _round_row(r: dict) -> dict:
    r = dict(r)
    for k, v in r.items():
        if isinstance(v, float):
            r[k] = round(v, 5)
    return r


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--spec", help="YAML expression-grid spec (see module docstring)")
    ap.add_argument("--start", help="Start date YYYY-MM-DD")
    ap.add_argument("--end", help="End date YYYY-MM-DD")
    ap.add_argument("--split", default=None,
                    help="Holdout split date (default: last third of the range)")
    ap.add_argument("--rank-metric", default="t_stat")
    ap.add_argument("--top-quantile", type=float, default=0.10)
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--out", default=None, help="Output folder")
    ap.add_argument("--hypothesis-id", default=None,
                    help="Overrides the spec's hypothesis_id")
    ap.add_argument("--no-ledger", action="store_true",
                    help="Skip the ledger event (exploratory runs only)")
    ap.add_argument("--list-features", nargs=2, metavar=("EXCHANGE", "SYMBOL"),
                    help="Show available frame features for a market and exit")
    args = ap.parse_args()

    if args.list_features:
        from kilimanjaro.data.feature_frame import available_features
        exchange, symbol = args.list_features
        info = available_features(exchange, symbol)
        print(json.dumps(info, indent=2) if info else f"no feature sources for {exchange}/{symbol}")
        return

    if not (args.spec and args.start and args.end):
        ap.error("--spec, --start, --end are required (or use --list-features)")

    spec = yaml.safe_load(Path(args.spec).read_text())
    hypothesis_id = args.hypothesis_id or spec.get("hypothesis_id")
    if hypothesis_id is None and not args.no_ledger:
        ap.error("no hypothesis_id in spec — pass --hypothesis-id or --no-ledger")

    combos = build_combos(spec)
    t = spec["target"]
    target = MarketConfig(t["exchange"], t["symbol"], t["execution_size"])
    schedule = ScheduleFilter(
        block_gmo_maintenance=(t["exchange"].upper() == "GMO"),
    )
    split = args.split or default_split(args.start, args.end)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    spec_name = Path(args.spec).stem
    scan_id = f"{spec_name}_{args.start}_{args.end}_{ts}"
    out_dir = Path(args.out) if args.out else Path(f"results/grid_scan/expr/{scan_id}")

    print(f"Expression grid scan: {spec_name} → {target.exchange}/{target.symbol}")
    print(f"Split: train {args.start} .. <{split}  |  holdout {split} .. {args.end}")
    print(f"Grid: {len(combos)} combos over {len(spec['expressions'])} expressions")
    print(f"Output: {out_dir}/")

    done = {"train": 0, "holdout": 0}

    def on_result(row: dict) -> None:
        w = row["window"]
        done[w] += 1
        hold = row.get("horizon")
        hold_str = f"H={hold}" if hold is not None else f"hold={row.get('max_hold_s')}"
        label = (f"{row.get('expr')} thr={row.get('threshold')}"
                 f" inv={row.get('invert')} {hold_str}")
        if row.get("status") == "ok":
            print(f"[{w:7s} {done[w]:4d}/{len(combos)}] {label}"
                  f" → n={row['n']} net={row['avg_net']:+.3f}bps t={row['t_stat']:+.2f}",
                  flush=True)
        else:
            print(f"[{w:7s} {done[w]:4d}/{len(combos)}] {label} → {row['status']}", flush=True)

    res = run_grid_split(
        combos, target, args.start, split, args.end,
        schedule=schedule,
        rank_metric=args.rank_metric,
        top_quantile=args.top_quantile,
        min_n=args.min_n,
        on_result=on_result,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    params = {
        "scan_id": scan_id,
        "spec_file": str(args.spec),
        "spec": spec,
        "start": args.start,
        "end": args.end,
        "windows": res["windows"],
        **res["params"],
    }
    (out_dir / "params.json").write_text(json.dumps(params, indent=2))

    rows = [_round_row(r) for r in res["rows"]]
    rows.sort(key=lambda r: -(r.get("holdout_rank_pct") or 0))
    stable = [r for r in rows if r["stable"]]
    write_csv(rows, out_dir / "output.csv", _CSV_FIELDS)
    write_csv(stable, out_dir / "output_stable.csv", _CSV_FIELDS)
    monthly = res["monthly_rows"]
    if monthly:
        write_csv(monthly, out_dir / "output_monthly.csv", list(monthly[0].keys()))
    print(f"\nSaved {out_dir}/output.csv  ({len(rows)} combos)")
    print(f"Saved {out_dir}/output_stable.csv  ({len(stable)} rank-stable candidates)")
    print(f"Saved {out_dir}/output_monthly.csv  ({len(monthly)} combo-months)")
    print(f"Verdicts: {res['verdict_counts']}")

    if stable:
        print(f"\nRank-stable candidates (top {args.top_quantile:.0%} of both windows):")
        for r in stable[:20]:
            hold = r.get("horizon")
            hold_str = f"H={hold}" if hold is not None else f"hold={r.get('max_hold_s')}"
            print(f"  {r['expr']} thr={r['threshold']} inv={r['invert']}"
                  f" age=[{r['min_age']},{r['max_age']}) {hold_str}"
                  f" | train {r['train_avg_net']:+.3f}bps t={r['train_t_stat']:+.2f}"
                  f" | holdout {r['holdout_avg_net']:+.3f}bps t={r['holdout_t_stat']:+.2f}"
                  f" n={r['holdout_n']}")
    else:
        print("\nNo rank-stable candidates — nothing survived both windows.")

    if args.no_ledger:
        print("\n(--no-ledger: run NOT recorded in ledger/events.jsonl)")
    else:
        from grid_ledger import record_grid_scan
        event = record_grid_scan(
            hypothesis_id=hypothesis_id,
            actor_id="grid-scan-expr",
            scan_id=scan_id,
            out_dir=out_dir,
            market=f"{t['exchange']}/{t['symbol']}",
            res=res,
        )
        print(f"\nLedger: {event['event_id']} scan_attached → {hypothesis_id}")
        print(f"  {event['details']['headline']}")

    print(f"\nScan ID: {scan_id}")


if __name__ == "__main__":
    main()
