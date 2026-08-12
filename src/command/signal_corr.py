"""cli.py signal-corr — stage 13: per-factor correlation screen.

Two modes:
  --spec <yaml>        screen one spec's factors on its own markets.
  --families <SYM>      screen EVERY factor family in cycle/12_specs (for base symbol
                       <SYM>, e.g. BTC) across one or more execution --venues, from the
                       fixed BINANCE leader.

For each factor it reports IC (Pearson + rank, across horizons), the value distribution,
and monthly IC stability; plus the cross-factor correlation matrix. Writes
cycle/13_signal_corr/<name>/{ic.csv,dist.csv,corr_matrix.csv,decay.csv}.
See doc/research-pipeline.md.

    python src/cli.py signal-corr --families BTC \
        --venues GMO:BTC_JPY:0.01,COINCHECK:BTC_SPOT:0.002 \
        --start 2025-08-01 --end 2026-06-30 --horizons 5,10,30,60,120,300,600

DECAY VIEW (`decay.csv`): one row per factor × market pair, two columns per horizon —
`corr<h>` (Pearson) and `ic<h>` (Spearman rank) — so a row reads left-to-right as the
factor's decay curve, with market_a (where the factor is COMPUTED) and market_b (whose
forward return it PREDICTS) as explicit columns. `--pairs` spans several base symbols in
one run and writes a single combined cycle/13_signal_corr/<SYM>_decay.csv:

    python src/cli.py signal-corr --families BTC --start ... --end ... \
        --pairs BINANCE:BTC_USDT:0.002>GMO:BTC_JPY:0.01,BINANCE:BTC_USDT:0.002>COINCHECK:BTC_SPOT:0.002
"""

from __future__ import annotations

import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from kilimanjaro.driver.signal_corr import (
    Factor,
    compute,
    decay_table,
    factors_from_blend_expr,
)

_RESULTS = Path("cycle/13_signal_corr")
_LEADER = {"exchange": "BINANCE", "symbol": "BTC_USDT", "execution_size": "0.002"}


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("signal-corr",
                       help="Stage 13: per-factor IC + distribution + cross-factor corr")
    p.add_argument("--spec", help="single spec (markets + blend expr or factors list)")
    p.add_argument("--families", help="base symbol (e.g. BTC): scan every 12_specs family")
    p.add_argument("--venues", default="GMO:BTC_JPY:0.01,COINCHECK:BTC_SPOT:0.002",
                   help="[--families] execution venues EXCH:SYM:SIZE,... (default the 2 BTC venues)")
    p.add_argument("--leader", default="BINANCE:BTC_USDT:0.002",
                   help="[--families] leader market EXCH:SYM:SIZE (default BINANCE:BTC_USDT:0.002)")
    p.add_argument("--pairs",
                   help="[--families] explicit leader>venue pairs, overriding --leader/--venues: "
                        "'BINANCE:ETH_USDT:0.1>GMO:ETH_JPY:0.3,BINANCE:XRP_USDT:150>BITBANK:XRP_SPOT:150'. "
                        "Lets one run span several base symbols; every row carries market_a/market_b.")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--horizons", default="5,10,30,60,120,300,600,900",
                   help="forward-return horizons in seconds")
    p.add_argument("--target-mid", default="b_mid",
                   help="feature-frame expr for the execution venue mid (default b_mid)")
    p.set_defaults(func=_run)


def _mkt(s: str) -> dict:
    e, sym, size = s.split(":")
    return {"exchange": e, "symbol": sym, "execution_size": size}


def _families_from_specs(base_sym: str) -> list[Factor]:
    """Collect every distinct factor (tagged by family) from cycle/12_specs for base_sym."""
    factors, seen = [], set()
    for fp in sorted(glob.glob("cycle/12_specs/*.yaml")):
        spec = yaml.safe_load(Path(fp).read_text())
        if spec["target"]["symbol"].split("_")[0] != base_sym:
            continue
        fam = spec.get("family", "?")
        for f in _spec_factors(spec, fam):
            if f.expr not in seen:
                seen.add(f.expr)
                factors.append(f)
    return factors


def _spec_factors(spec: dict, fam: str) -> list[Factor]:
    if spec.get("factors"):  # explicit list of {name, expr}
        return [Factor(f["name"], f["expr"], fam) for f in spec["factors"]]
    exprs = spec["expressions"]
    # a single composite `zscore(Σ wᵢ·(termᵢ), W)` → split into its factor terms
    if len(exprs) == 1 and "*(" in exprs[0]["expr"]:
        return [Factor(f.name, f.expr, fam) for f in factors_from_blend_expr(exprs[0]["expr"])]
    from kilimanjaro.driver.signal_corr import _slug  # each expression is its own factor;
    return [Factor(e.get("label") or _slug(e["expr"]), e["expr"], fam) for e in exprs]  # prefer the spec label


def _run(args) -> int:
    horizons = [int(h) for h in args.horizons.split(",")]
    if args.families:
        factors = _families_from_specs(args.families)
        # A "pair" is (leader=marketA, venue=marketB): the factors are computed on A and
        # scored against B's forward return. --pairs lets one run span several base
        # symbols; --leader/--venues is the single-leader shorthand.
        if args.pairs:
            pairs = [(_mkt(a), _mkt(b)) for a, b in
                     (p.split(">") for p in args.pairs.split(","))]
        else:
            pairs = [(_mkt(args.leader), _mkt(v)) for v in args.venues.split(",")]
        fams = sorted({f.family for f in factors})
        print(f"signal-corr FAMILY SCAN: {args.families} | {len(factors)} factors "
              f"({len(fams)} families) × {len(horizons)} horizons × {len(pairs)} pairs | "
              f"{args.start}..{args.end}")
        for a, b in pairs:
            print(f"  {a['exchange']}:{a['symbol']} -> {b['exchange']}:{b['symbol']}")
        cross, decays = {}, []
        for a, b in pairs:
            ma, mb = f"{a['exchange']}:{a['symbol']}", f"{b['exchange']}:{b['symbol']}"
            res = compute({"a": dict(a), "b": dict(b)}, factors,
                          args.target_mid, args.start, args.end, horizons)
            tag = f"{ma}_{mb}".replace(":", "_")
            _write(res, _RESULTS / f"{args.families}_{tag}")
            d = decay_table(res.ic, ma, mb)
            d.to_csv(_RESULTS / f"{args.families}_{tag}" / "decay.csv", index=False)
            decays.append(d)
            _print_decay(ma, mb, d, horizons)
            cross[mb] = res.dist.set_index("factor")["best_rank_ic"]
        # One combined survey table across every pair — the artifact to read/compare.
        combined = _RESULTS / f"{args.families}_decay.csv"
        pd.concat(decays, ignore_index=True).to_csv(combined, index=False)
        print(f"\n  wrote {combined}  ({sum(len(d) for d in decays)} rows: factor × market pair)")
        _print_cross(factors, cross)
        return 0

    # single-spec mode
    spec = yaml.safe_load(Path(args.spec).read_text())
    factors = _spec_factors(spec, spec.get("family", ""))
    print(f"signal-corr: {Path(args.spec).stem} — {len(factors)} factors × "
          f"{len(horizons)} horizons | {args.start}..{args.end}")
    res = compute(spec["markets"], factors, args.target_mid, args.start, args.end, horizons)
    out = _RESULTS / Path(args.spec).stem
    _write(res, out)
    ma = "{exchange}:{symbol}".format(**spec["markets"]["a"])
    mb = "{exchange}:{symbol}".format(**spec["markets"].get("b", spec["target"]))
    d = decay_table(res.ic, ma, mb)
    d.to_csv(out / "decay.csv", index=False)
    _print_decay(ma, mb, d, horizons)
    _print_scorecard(spec["target"]["symbol"], res)
    return 0


def _write(res, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    res.ic.to_csv(out / "ic.csv", index=False)
    res.summary().to_csv(out / "dist.csv", index=False)
    res.corr.to_csv(out / "corr_matrix.csv")


def _print_decay(market_a: str, market_b: str, d, horizons: list[int]) -> None:
    """Console view of the decay profile: rank IC across horizons, one row per factor.

    Only rank IC is printed — 8 horizons x (Pearson, Spearman) does not fit a terminal.
    decay.csv carries both (corr<h> = Pearson, ic<h> = Spearman)."""
    print(f"\n===== {market_a} -> {market_b} — rank-IC decay (sorted by |peak|) =====")
    print(f"  {'factor':<30} {'peak@':>6} " + "".join(f"{('h'+str(h)):>9}" for h in horizons))
    for _, r in d.iterrows():
        cells = "".join(f"{r[f'ic{h}']:>+9.4f}" if r[f"ic{h}"] == r[f"ic{h}"] else f"{'-':>9}"
                        for h in horizons)
        # peak_h is absent when a factor had no finite IC at any horizon (e.g. a column
        # that is all-NaN on this market pair) — show it rather than crashing on it.
        peak = f"{int(r.peak_h)}" if pd.notna(r.peak_h) else "-"
        print(f"  {r.factor:<30} {peak:>6}{cells}")


def _print_scorecard(venue: str, res) -> None:
    print(f"\n===== {venue} — factor scorecard (sorted by |rank_IC|) =====")
    print(f"  {'family':<13} {'factor':<30} {'rankIC':>8} {'@h':>4} {'mo+%':>5} "
          f"{'moIC':>7} {'std':>6} {'skew':>6} {'act%':>5}")
    for _, r in res.summary().iterrows():
        mo = f"{r.ic_month_pos*100:.0f}" if r.ic_month_pos == r.ic_month_pos else "  -"
        print(f"  {str(r.family):<13} {r.factor:<30} {r.best_rank_ic:>+8.4f} {int(r.best_h):>4} "
              f"{mo:>5} {r.ic_month_mean:>+7.4f} {r.val_std:>6.2f} {r.val_skew:>+6.2f} "
              f"{r.active_frac*100:>4.1f}")
    m = res.corr.to_numpy(); off = m[~np.eye(len(m), dtype=bool)]
    print(f"  mean |cross-factor corr|: {np.nanmean(np.abs(off)):.3f} "
          f"(lower → more orthogonal → more blend headroom)")


def _print_cross(factors: list[Factor], cross: dict) -> None:
    if len(cross) < 2:
        return
    print(f"\n===== CROSS-VENUE best rank_IC per factor =====")
    venues = list(cross)
    fam = {f.name: f.family for f in factors}
    print(f"  {'family':<13} {'factor':<30} " + "".join(f"{v.split(':')[0]:>12}" for v in venues))
    order = sorted(fam, key=lambda n: -max(abs(cross[v].get(n, 0) or 0) for v in venues))
    for n in order:
        vals = "".join(f"{(cross[v].get(n, float('nan'))):>+12.4f}" for v in venues)
        print(f"  {fam.get(n,''):<13} {n:<30} {vals}")
