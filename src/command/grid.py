"""`cli.py grid` — parameter-grid scans (train/holdout split + rank stability).

Three flavours, one shared core (driver/scan_runner.run_grid_scan):

    python src/cli.py grid flip --market btc --start 2025-07-01 --end 2026-06-30
    python src/cli.py grid expr --spec my_grid.yaml --start ... --end ...
    python src/cli.py grid obi  --market eth --start ... --end ...

Replaces the old tools/grid_scan{,_expr,_horizon}.py, which were three copies of
the same run/write/ledger pipeline differing only in how combos are built.
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

import yaml

from kilimanjaro.driver import flip_grid, ledger_io, obi_grid
from kilimanjaro.driver.scan_runner import build_combos, run_grid_scan, run_spec
from kilimanjaro.framework import MarketConfig, RollingDrawdownGate, default_split
from kilimanjaro.framework.orion_schedule import OrionScheduleFilter

_RESULTS = Path("results/grid_scan")
_FLIP_HYP = "hyp-20260703-002"  # schedule-adaptive flip-scan spec (box×step×sched)
_OBI_HYP = {"eth": "hyp-20260713-001", "btc": "hyp-20260618-001"}


# ── argparse ─────────────────────────────────────────────────────────────────
def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("grid", help="Parameter-grid scans (train/holdout + rank stability)")
    gsub = p.add_subparsers(dest="grid_cmd", required=True)

    pf = gsub.add_parser("flip", help="Range-bar SAR flip grid (box × step × schedule)")
    pf.add_argument("--market", required=True, choices=list(flip_grid.MARKET_CONFIGS))
    _common(pf)
    pf.add_argument("--size", default=None, help="Override execution_size")
    pf.add_argument("--dd-window-seconds", type=float, default=flip_grid.DD_WINDOW_SECONDS)
    pf.add_argument("--dd-threshold-bps", type=float, default=flip_grid.DD_THRESHOLD_BPS)
    pf.add_argument("--dd-cooldown-seconds", type=float, default=flip_grid.DD_COOLDOWN_SECONDS)
    pf.add_argument("--hypothesis-id", default=_FLIP_HYP)
    pf.set_defaults(func=_run_flip)

    pe = gsub.add_parser("expr", help="Expression grid from a YAML spec")
    pe.add_argument("--spec", help="YAML expression-grid spec")
    pe.add_argument("--start"); pe.add_argument("--end")
    _common_opts(pe)
    pe.add_argument("--hypothesis-id", default=None, help="Overrides the spec's hypothesis_id")
    pe.add_argument("--list-features", nargs=2, metavar=("EXCHANGE", "SYMBOL"),
                    help="Show available frame features for a market and exit")
    pe.set_defaults(func=_run_expr)

    po = gsub.add_parser("obi", help="OBI-age grid (depth × halflife × age × threshold × horizon)")
    po.add_argument("--market", required=True, choices=list(obi_grid.MARKET_CONFIGS))
    _common(po)
    po.add_argument("--hypothesis-id", default=None,
                    help="Default: per-market; markets without a spec require this or --no-ledger")
    po.set_defaults(func=_run_obi)


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    _common_opts(p)


def _common_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--split", default=None, help="Holdout split date (default: last third)")
    p.add_argument("--rank-metric", default="t_stat")
    p.add_argument("--top-quantile", type=float, default=0.10)
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--out", default=None, help="Output folder")
    p.add_argument("--no-ledger", action="store_true", help="Skip the ledger event")


# ── shared helpers ───────────────────────────────────────────────────────────
def _progress(n_combos: int):
    done = {"train": 0, "holdout": 0}

    def on_result(row: dict) -> None:
        w = row["window"]
        done[w] += 1
        if done[w] % 100 == 0 or row.get("status") not in ("ok", "no_signals"):
            print(f"[{w:7s} {done[w]:5d}/{n_combos}] status={row.get('status')}", flush=True)

    return on_result


def _finish(res: dict, out_dir: Path, scan_id: str, args, market: str, actor: str) -> int:
    stable = [r for r in res["rows"] if r.get("stable")]
    print(f"\nSaved {out_dir}/  ({len(res['rows'])} combos, {len(stable)} rank-stable)")
    print(f"Verdicts: {res.get('verdict_counts')}")
    if args.no_ledger:
        print("(--no-ledger: not recorded)")
    elif args.hypothesis_id is None:
        print("WARNING: no hypothesis-id and not --no-ledger — skipped ledger; pass --hypothesis-id")
    else:
        ev = ledger_io.record_scan_attached(
            hypothesis_id=args.hypothesis_id, actor_id=actor,
            scan_id=scan_id, out_dir=out_dir, market=market, res=res,
        )
        print(f"Ledger: {ev['event_id']} scan_attached → {args.hypothesis_id}")
        print(f"  {ev['details']['headline']}")
    print(f"Scan ID: {scan_id}")
    return 0


# ── flavours ─────────────────────────────────────────────────────────────────
def _run_flip(args: argparse.Namespace) -> int:
    cfg = dict(flip_grid.MARKET_CONFIGS[args.market])
    if args.size:
        cfg["execution_size"] = args.size
    target = MarketConfig(cfg["target_exchange"], cfg["target_symbol"], cfg["execution_size"])
    combos = flip_grid.build_combos(cfg)
    gate = RollingDrawdownGate(
        window_seconds=args.dd_window_seconds,
        threshold_bps=args.dd_threshold_bps,
        cooldown_seconds=args.dd_cooldown_seconds,
    )
    print(f"Flip grid: {args.market.upper()} — {len(combos)} combos")
    res, out_dir, scan_id = run_grid_scan(
        combos, target, args.start, args.end,
        results_root=_RESULTS / "flip", scan_name=args.market.upper(),
        out_dir=Path(args.out) if args.out else None,
        csv_fields=flip_grid.CSV_FIELDS,
        split=args.split, gates=[gate],
        param_meta={"market": args.market, "grid": {"boxes": flip_grid.BOXES,
                    "steps": flip_grid.STEPS, "schedule_source": "orion.schedule"}},
        rank_metric=args.rank_metric, top_quantile=args.top_quantile, min_n=args.min_n,
        on_result=_progress(len(combos)),
    )
    return _finish(res, out_dir, scan_id, args, args.market, "grid-flip")


def _run_expr(args: argparse.Namespace) -> int:
    if args.list_features:
        from kilimanjaro.data.feature_frame import available_features
        info = available_features(*args.list_features)
        print(json.dumps(info, indent=2) if info else f"no feature sources for {args.list_features}")
        return 0
    if not (args.spec and args.start and args.end):
        print("--spec, --start, --end are required (or use --list-features)")
        return 2
    spec = yaml.safe_load(Path(args.spec).read_text())
    args.hypothesis_id = args.hypothesis_id or spec.get("hypothesis_id")
    combos = build_combos(spec)
    print(f"Expression grid: {Path(args.spec).stem} — {len(combos)} combos")
    res, out_dir, scan_id = run_spec(
        spec, args.start, args.end,
        Path(args.out).parent if args.out else _RESULTS / "expr",
        split=args.split, rank_metric=args.rank_metric,
        top_quantile=args.top_quantile, min_n=args.min_n,
        scan_name=Path(args.spec).stem, on_result=_progress(len(combos)),
    )
    return _finish(res, out_dir, scan_id, args, spec["target"]["symbol"], "grid-expr")


def _run_obi(args: argparse.Namespace) -> int:
    cfg = obi_grid.MARKET_CONFIGS[args.market]
    if args.hypothesis_id is None:
        args.hypothesis_id = _OBI_HYP.get(args.market)
    target = MarketConfig(cfg["target_exchange"], cfg["target_symbol"], cfg["execution_size"])
    combos = obi_grid.build_combos(cfg)
    schedule = OrionScheduleFilter("nofilter") if cfg["target_exchange"].upper() == "GMO" else None
    split = args.split or default_split(args.start, args.end)
    train_days = max((datetime.date.fromisoformat(split)
                      - datetime.date.fromisoformat(args.start)).days, 1)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    scan_id = f"{args.market.upper()}_{args.start}_{args.end}_{ts}"
    out_dir = Path(args.out) if args.out else _RESULTS / "horizon" / scan_id
    age_stats: list[dict] = []
    print(f"OBI-age grid: {args.market.upper()} — {len(combos)} combos")
    res, out_dir, scan_id = run_grid_scan(
        combos, target, args.start, args.end,
        results_root=_RESULTS / "horizon", scan_id=scan_id, out_dir=out_dir,
        scan_name=args.market.upper(), csv_fields=obi_grid.CSV_FIELDS,
        split=split, schedule=schedule, row_transform=obi_grid.label_row,
        on_data_loaded=obi_grid.make_age_hist_hook(out_dir, train_days, age_stats),
        rank_metric=args.rank_metric, top_quantile=args.top_quantile, min_n=args.min_n,
        on_result=_progress(len(combos)),
    )
    obi_grid.write_age_stats(out_dir, age_stats)
    return _finish(res, out_dir, scan_id, args, args.market, "grid-obi")
