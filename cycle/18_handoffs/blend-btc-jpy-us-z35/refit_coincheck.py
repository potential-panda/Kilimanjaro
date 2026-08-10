#!/usr/bin/env python
"""Refit the blend composite weights on COINCHECK BTC_SPOT (not GMO).

composite_coincheck.yaml reuses the GMO-fit weights (the basis leg is tuned for
BINANCE/GMO), so its numbers are a floor. This refits each factor's IC-weight
against COINCHECK's own forward return, writes composite_coincheck_refit.yaml, and
runs it US-hours (episode + triple-barrier, z=3.5/4.5) so you can compare to the
GMO-fit floor (episode +3.03/+3.93, TB +/-10 +3.19/+4.47 holdout).

    python cycle/18_handoffs/blend-btc-jpy-us-z35/refit_coincheck.py
"""

from __future__ import annotations

import copy
from glob import glob
from pathlib import Path

import yaml

from kilimanjaro.driver.blend import blend, composite_spec, factors_from_specs
from kilimanjaro.driver.scan_runner import run_spec
from kilimanjaro.framework import MarketConfig, default_split
from kilimanjaro.framework.schedule import ScheduleFilter

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[2]
CC = {"exchange": "COINCHECK", "symbol": "BTC_SPOT", "execution_size": "0.002"}
US = [22, 23, 0, 1, 2]
START, END = "2025-07-01", "2026-06-30"
HORIZON = 300


def _us_schedule() -> ScheduleFilter:
    blocked = [h for h in range(24) if h not in set(US)]
    return ScheduleFilter(block_weekday_hours=blocked, block_weekend_hours=blocked,
                          block_gmo_maintenance=False, name="jst_us")


def main() -> None:
    specs = [yaml.safe_load(Path(p).read_text()) for p in sorted(glob(str(_ROOT / "cycle/12_specs/*BTC_JPY.yaml")))]
    old = ("GMO", "BTC_JPY")
    for s in specs:                       # retarget the exec/basis leg + target to COINCHECK
        for alias, m in s["markets"].items():
            if (m["exchange"], m["symbol"]) == old:
                s["markets"][alias] = dict(CC)
        s["target"] = dict(CC)

    target = MarketConfig(CC["exchange"], CC["symbol"], CC["execution_size"])
    split = default_split(START, END)
    factors = factors_from_specs(specs)

    # Refit weights = COINCHECK train ICs (evaluated on COINCHECK holdout).
    res = blend(factors, target, START, split, END, HORIZON)
    weights = {p["factor"]: p["weight"] for p in res["per_factor"]}
    print(f"refit blend: {len(factors)} factors -> COINCHECK/BTC_SPOT | "
          f"holdout blend IC {res['blend_holdout_ic']:+.4f} "
          f"(best single {res['best_single_holdout_ic']:.4f})")

    # Persist the refit composite (episode form; barriers kept for TB runs).
    comp = composite_spec(specs, factors, weights, thresholds=[3.5, 4.5],
                          horizon=HORIZON, min_abs_weight=0.01,
                          return_calc="episode", barrier_bps=10.0, time_cap=300)
    comp["barriers"] = [10.0, 20.0]
    comp["time_caps"] = [300]
    (_HERE / "composite_coincheck_refit.yaml").write_text(yaml.safe_dump(comp, sort_keys=False))

    out = _ROOT / "cycle/15_return_eval/blend/coincheck_refit"
    for label, rc in (("episode", "episode"), ("triple_barrier", "triple_barrier")):
        spec = copy.deepcopy(comp)
        spec["return_calc"] = rc
        r, _, _ = run_spec(spec, START, END, results_root=out / label, split=split,
                           scan_name=f"refit_{label}", schedule=_us_schedule())
        print(f"\n  exit={label} (US hours) — REFIT weights")
        print(f"  {'thr':>4} {'barr':>5} {'ho_n':>6} {'ho_avg_net':>11} "
              f"{'ho_hit':>7} {'ho_total':>10} {'sharpe':>7}")
        for row in sorted(r["rows"], key=lambda x: (x.get("threshold", 0), x.get("barrier_bps") or 0)):
            print(f"  {row.get('threshold'):>4} {str(row.get('barrier_bps') or '-'):>5} "
                  f"{row.get('holdout_n_episodes', row.get('holdout_n', 0)):>6} "
                  f"{_f(row.get('holdout_avg_net')):>11} {_f(row.get('holdout_hit_rate_net')):>7} "
                  f"{_f(row.get('holdout_total_net_bps')):>10} "
                  f"{_f(row.get('holdout_sharpe_per_trade')):>7}")
    print(f"\n  -> {out}   composite: {_HERE / 'composite_coincheck_refit.yaml'}")
    print("  compare to GMO-fit floor: episode +3.03/+3.93, TB +/-10 +3.19/+4.47 (holdout)")


def _f(v) -> str:
    return f"{v:.3f}" if isinstance(v, (int, float)) and v == v else "-"


if __name__ == "__main__":
    main()
