"""`cli.py grid` — parameter-grid scans (train/holdout split + rank stability).

Two flavours, one shared core (driver/scan_runner.run_grid_scan):

    python src/cli.py grid flip    --market btc --start 2025-07-01 --end 2026-06-30
    python src/cli.py grid horizon --spec my_grid.yaml --start ... --end ...

`horizon` runs any expression spec (formula signals over the feature frame,
fixed-horizon returns, with an episode re-test tier). OBI is just an expression
over an OBI column, so it is one such spec — there is no separate `obi` flavour.
The OBI age-distribution diagnostic that flavour used to bundle is now the
signal-agnostic `cli.py learn --op age` command.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from kilimanjaro.driver import flip_grid, ledger_io
from kilimanjaro.driver.scan_runner import build_combos, run_grid_scan, run_spec
from kilimanjaro.framework import MarketConfig, RollingDrawdownGate

_RESULTS = Path("cycle/15_return_eval/grid_scan")
_FLIP_HYP = "hyp-20260703-002"  # schedule-adaptive flip-scan spec (box×step×sched)


# ── argparse ─────────────────────────────────────────────────────────────────
def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "grid", help="Parameter-grid scans (train/holdout + rank stability)"
    )
    gsub = p.add_subparsers(dest="grid_cmd", required=True)

    pf = gsub.add_parser("flip", help="Range-bar SAR flip grid (box × step × schedule)")
    pf.add_argument("--market", required=True, choices=list(flip_grid.MARKET_CONFIGS))
    _common(pf)
    pf.add_argument("--size", default=None, help="Override execution_size")
    pf.add_argument(
        "--dd-window-seconds", type=float, default=flip_grid.DD_WINDOW_SECONDS
    )
    pf.add_argument(
        "--dd-threshold-bps", type=float, default=flip_grid.DD_THRESHOLD_BPS
    )
    pf.add_argument(
        "--dd-cooldown-seconds", type=float, default=flip_grid.DD_COOLDOWN_SECONDS
    )
    pf.add_argument("--hypothesis-id", default=_FLIP_HYP)
    pf.set_defaults(func=_run_flip)

    ph = gsub.add_parser(
        "horizon",
        help="Expression grid from a YAML spec "
        "(formula signals, fixed-horizon returns)",
    )
    ph.add_argument("--spec", help="YAML expression-grid spec")
    ph.add_argument("--start")
    ph.add_argument("--end")
    _common_opts(ph)
    ph.add_argument(
        "--hypothesis-id", default=None, help="Overrides the spec's hypothesis_id"
    )
    ph.add_argument(
        "--no-drawdown-gate",
        action="store_true",
        help="Disable the default rolling-drawdown risk gate (applies to "
        "episode return_calc; fixed_horizon is ungated by nature)",
    )
    ph.add_argument(
        "--list-features",
        nargs=2,
        metavar=("EXCHANGE", "SYMBOL"),
        help="Show available frame features for a market and exit",
    )
    ph.set_defaults(func=_run_horizon)


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    _common_opts(p)


def _common_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--split", default=None, help="Holdout split date (default: last third)"
    )
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
            print(
                f"[{w:7s} {done[w]:5d}/{n_combos}] status={row.get('status')}",
                flush=True,
            )

    return on_result


def _finish(
    res: dict, out_dir: Path, scan_id: str, args, market: str, actor: str
) -> int:
    stable = [r for r in res["rows"] if r.get("stable")]
    print(f"\nSaved {out_dir}/  ({len(res['rows'])} combos, {len(stable)} rank-stable)")
    print(f"Verdicts: {res.get('verdict_counts')}")
    if args.no_ledger:
        print("(--no-ledger: not recorded)")
    elif args.hypothesis_id is None:
        print(
            "WARNING: no hypothesis-id and not --no-ledger — skipped ledger; pass --hypothesis-id"
        )
    else:
        ev = ledger_io.record_scan_attached(
            hypothesis_id=args.hypothesis_id,
            actor_id=actor,
            scan_id=scan_id,
            out_dir=out_dir,
            market=market,
            res=res,
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
    target = MarketConfig(
        cfg["target_exchange"], cfg["target_symbol"], cfg["execution_size"]
    )
    combos = flip_grid.build_combos(cfg)
    gate = RollingDrawdownGate(
        window_seconds=args.dd_window_seconds,
        threshold_bps=args.dd_threshold_bps,
        cooldown_seconds=args.dd_cooldown_seconds,
    )
    print(f"Flip grid: {args.market.upper()} — {len(combos)} combos")
    res, out_dir, scan_id = run_grid_scan(
        combos,
        target,
        args.start,
        args.end,
        results_root=_RESULTS / "flip",
        scan_name=args.market.upper(),
        out_dir=Path(args.out) if args.out else None,
        csv_fields=flip_grid.CSV_FIELDS,
        split=args.split,
        gates=[gate],
        param_meta={
            "market": args.market,
            "grid": {
                "boxes": flip_grid.BOXES,
                "steps": flip_grid.STEPS,
                "schedule_source": "orion.schedule",
            },
        },
        rank_metric=args.rank_metric,
        top_quantile=args.top_quantile,
        min_n=args.min_n,
        on_result=_progress(len(combos)),
    )
    return _finish(res, out_dir, scan_id, args, args.market, "grid-flip")


def _run_horizon(args: argparse.Namespace) -> int:
    if args.list_features:
        from kilimanjaro.data.feature_frame import available_features

        info = available_features(*args.list_features)
        print(
            json.dumps(info, indent=2)
            if info
            else f"no feature sources for {args.list_features}"
        )
        return 0
    if not (args.spec and args.start and args.end):
        print("--spec, --start, --end are required (or use --list-features)")
        return 2
    spec = yaml.safe_load(Path(args.spec).read_text())
    args.hypothesis_id = args.hypothesis_id or spec.get("hypothesis_id")
    if args.no_drawdown_gate:  # CLI opt-out overrides the spec's default-on gate
        spec["drawdown_gate"] = False
    combos = build_combos(spec)
    print(f"Horizon grid: {Path(args.spec).stem} — {len(combos)} combos")
    res, out_dir, scan_id = run_spec(
        spec,
        args.start,
        args.end,
        Path(args.out).parent if args.out else _RESULTS / "horizon",
        split=args.split,
        rank_metric=args.rank_metric,
        top_quantile=args.top_quantile,
        min_n=args.min_n,
        scan_name=Path(args.spec).stem,
        on_result=_progress(len(combos)),
    )
    return _finish(
        res, out_dir, scan_id, args, spec["target"]["symbol"], "grid-horizon"
    )
