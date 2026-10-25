"""cli.py op-sweep — stage 13: the wide operator x base-column factor catalog.

Measures every (base column, operator, param, zscore window) combination identically:
corr/ic (prediction) and gross/net/se in bps at real bid/ask (tradability), across a
horizon grid. Measurement only — selection and fitting belong to stage 14.

    python src/cli.py op-sweep --config cycle/12_specs/probes/op_sweep.yaml \
        --start 2025-08-01 --end 2026-02-28 --workers 16

Writes cycle/13_signal_corr/op_sweep_<A>_<B>/sweep.csv. Defaults to the TRAIN window
so a factor picked by eye off this table has not already seen the stage-14 holdout.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

from kilimanjaro.driver.op_sweep import run_sweep

_RESULTS = Path("cycle/13_signal_corr")


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "op-sweep", help="Stage 13: wide op x base-column factor catalog"
    )
    p.add_argument(
        "--config",
        default="cycle/12_specs/probes/op_sweep.yaml",
        help="bases + ops config (see the probes dir)",
    )
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--horizons", default="5,10,30,60,120,300,600,900")
    p.add_argument(
        "--fire-threshold",
        type=float,
        default=2.0,
        help="|z| counted as a fire for gross/net (default 2.0)",
    )
    p.add_argument("--cost-bps", type=float, default=1.0)
    p.add_argument(
        "--workers", type=int, default=None, help="processes (default min(16, cores))"
    )
    p.add_argument(
        "--limit-bases",
        type=int,
        default=None,
        help="only the first N bases — for a smoke test",
    )
    p.add_argument("--out", default=None)
    p.set_defaults(func=_run)


def _run(args) -> int:
    cfg = yaml.safe_load(Path(args.config).read_text())
    if args.limit_bases:
        cfg["bases"] = dict(list(cfg["bases"].items())[: args.limit_bases])
    horizons = [int(h) for h in args.horizons.split(",")]

    df = run_sweep(
        cfg,
        args.start,
        args.end,
        horizons,
        fire_threshold=args.fire_threshold,
        cost_bps=args.cost_bps,
        workers=args.workers,
    )

    tag = f"{cfg['market_a']}_{cfg['market_b']}".replace(":", "_")
    out = Path(args.out) if args.out else _RESULTS / f"op_sweep_{tag}"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "sweep.csv", index=False)

    errs = df[df.get("error").notna()] if "error" in df.columns else df.iloc[:0]
    ok = df.drop(errs.index) if len(errs) else df
    print(f"\n  rows: {len(df):,}  ({len(errs):,} errored)")
    if len(errs):
        for e in errs.error.value_counts().head(5).items():
            print(f"    {e[1]:>5}x  {e[0][:110]}")
    if len(ok):
        print("\n  ── top 15 by |peak IC| ──")
        _show(
            ok.reindex(
                ok.peak_ic.abs().sort_values(ascending=False, na_position="last").index
            ).head(15)
        )
        print("\n  ── top 15 by best NET bps (>0 clears the spread) ──")
        _show(ok.sort_values("best_net", ascending=False, na_position="last").head(15))
    print(f"\n  wrote {out}/sweep.csv")
    return 0


def _show(d: pd.DataFrame) -> None:
    print(
        f"  {'factor':<44} {'peakIC':>8} {'@h':>4} {'bestNet':>8} {'@h':>4} "
        f"{'se':>7} {'nFire':>9}"
    )
    for _, r in d.iterrows():
        se = (
            r.get(f"se{int(r.best_net_h)}")
            if pd.notna(r.get("best_net_h"))
            else float("nan")
        )
        print(
            f"  {r.factor!s:<44} {r.peak_ic:>+8.4f} {_i(r.peak_ic_h):>4} "
            f"{r.best_net:>+8.2f} {_i(r.best_net_h):>4} {se if pd.notna(se) else float('nan'):>7.2f} "
            f"{_i(r.n_fire):>9}"
        )


def _i(x) -> str:
    return "-" if pd.isna(x) else str(int(x))
