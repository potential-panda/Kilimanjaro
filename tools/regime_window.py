#!/usr/bin/env python3
"""Static, history-derived favorable trading window per market (or
feature/target pair): which 30-min JST buckets are "good" -- high
volatility relative to spread, strong/stable lead-lag (if cross-market),
and easy to fill (if limit-only) -- based on separately-computed,
already-validated wave artifacts (markets/waves/*.csv).

Deliberately static, never computed live on the window being scored.
Two reasons, confirmed with the user while building this:
1. The whole value of the wave indicators is that the daily pattern is
   stable and knowable *in advance* -- a live filter would only tell you
   "today looked good" after part of the day already happened.
2. Picking the "good" window from the same data the signal is being
   scored on would be the regime-selection analogue of the single-cell
   horizon/threshold overfitting tools/threshold_evaluator.py already
   guards against. Using a separately-built artifact avoids that.

A bucket is favorable only if it's in the top tercile of EVERY *provided*
criterion (intersection, not union) -- "good" requires every available
signal to agree, not just one of them. Criteria that don't apply (e.g. no
lead-lag wave for a same-market hypothesis) are simply omitted, not
assumed favorable.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).parent.parent
_DEFAULT_WAVES_DIR = _ROOT / "markets" / "waves"
_DEFAULT_REGIMES_DIR = _ROOT / "markets" / "regimes"
_DEFAULT_TERCILE = 1 / 3


def _top_tercile_buckets(df: pd.DataFrame, bucket_col: str, value_col: str, tercile: float) -> set[int]:
    threshold = df[value_col].quantile(1 - tercile)
    return set(df.loc[df[value_col] >= threshold, bucket_col].astype(int))


def compute_favorable_buckets(
    vol_wave: pd.DataFrame | None = None,
    vol_col: str = "p_gt_1x",
    lead_lag_wave: pd.DataFrame | None = None,
    lead_lag_col: str = "mean_best_corr",
    fill_wave: pd.DataFrame | None = None,
    fill_cols: tuple[str, ...] = (),
    fill_horizon_seconds: int = 120,
    tercile: float = _DEFAULT_TERCILE,
) -> dict:
    """Returns {"favorable_buckets": sorted list, "criteria": {...}}.
    Each criterion's per-bucket pass/fail and threshold are recorded, so
    the artifact discloses exactly what was checked and what wasn't --
    never silently treated as favorable for a criterion that wasn't
    actually evaluated."""
    criteria: dict = {}
    bucket_sets: list[set[int]] = []

    if vol_wave is not None:
        # volatility_wave.py's CSV has one row per (bucket, horizon) --
        # use the smallest tested horizon, the most reactive measure.
        sub = vol_wave[vol_wave["horizon_seconds"] == vol_wave["horizon_seconds"].min()]
        buckets = _top_tercile_buckets(sub, "bucket", vol_col, tercile)
        criteria["volatility_spread"] = {"column": vol_col, "tercile": tercile, "n_favorable_buckets": len(buckets)}
        bucket_sets.append(buckets)

    if lead_lag_wave is not None:
        buckets = _top_tercile_buckets(lead_lag_wave, "bucket", lead_lag_col, tercile)
        criteria["lead_lag"] = {"column": lead_lag_col, "tercile": tercile, "n_favorable_buckets": len(buckets)}
        bucket_sets.append(buckets)

    if fill_wave is not None and fill_cols:
        # fill_probability.py's CSV has one row per (bucket, horizon) --
        # without filtering to one horizon first, a bucket would look
        # "favorable" if ANY of the 5 tested horizons cleared the
        # threshold (longer horizons trivially have higher fill
        # probability), which silently defeats the whole filter -- found
        # for real: every single bucket came back "favorable" before this
        # fix. Match the horizon actually used elsewhere in this project.
        sub = fill_wave[fill_wave["horizon_seconds"] == fill_horizon_seconds].copy()
        sub["_fill_combined"] = sub[list(fill_cols)].mean(axis=1)
        buckets = _top_tercile_buckets(sub, "bucket", "_fill_combined", tercile)
        criteria["fill_ease"] = {"columns": list(fill_cols), "horizon_seconds": fill_horizon_seconds, "tercile": tercile, "n_favorable_buckets": len(buckets)}
        bucket_sets.append(buckets)

    if not bucket_sets:
        raise ValueError("no criteria provided -- need at least one of vol_wave/lead_lag_wave/fill_wave")

    favorable = set.intersection(*bucket_sets)
    return {"favorable_buckets": sorted(favorable), "criteria": criteria}


def build_and_write(
    key: str,
    vol_wave_path: Path | None = None,
    lead_lag_wave_path: Path | None = None,
    fill_wave_path: Path | None = None,
    fill_cols: tuple[str, ...] = ("p_fill_buy_x5bps", "p_fill_sell_x5bps"),
    fill_horizon_seconds: int = 120,
    bucket_minutes: int = 30,
    tercile: float = _DEFAULT_TERCILE,
    regimes_dir: Path = _DEFAULT_REGIMES_DIR,
) -> Path:
    result = compute_favorable_buckets(
        vol_wave=pd.read_csv(vol_wave_path) if vol_wave_path else None,
        lead_lag_wave=pd.read_csv(lead_lag_wave_path) if lead_lag_wave_path else None,
        fill_wave=pd.read_csv(fill_wave_path) if fill_wave_path else None,
        fill_cols=fill_cols,
        fill_horizon_seconds=fill_horizon_seconds,
        tercile=tercile,
    )
    artifact = {
        "key": key,
        "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bucket_minutes": bucket_minutes,
        "sources": {
            "volatility_wave": str(vol_wave_path) if vol_wave_path else None,
            "lead_lag_wave": str(lead_lag_wave_path) if lead_lag_wave_path else None,
            "fill_probability_wave": str(fill_wave_path) if fill_wave_path else None,
        },
        **result,
    }
    regimes_dir.mkdir(parents=True, exist_ok=True)
    path = regimes_dir / f"{key}.json"
    path.write_text(json.dumps(artifact, indent=2))
    return path


def load_regime_artifact(key: str, regimes_dir: Path = _DEFAULT_REGIMES_DIR) -> dict | None:
    path = regimes_dir / f"{key}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def regime_key(feature_exchange: str | None, feature_symbol: str | None, target_exchange: str, target_symbol: str) -> str:
    if feature_exchange is None or (feature_exchange, feature_symbol) == (target_exchange, target_symbol):
        return f"{target_exchange}_{target_symbol}"
    return f"{feature_exchange}_{feature_symbol}__{target_exchange}_{target_symbol}"


def bucket_of_jst_second(jst_second_of_day: int, bucket_minutes: int = 30) -> int:
    return int(jst_second_of_day // (bucket_minutes * 60))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a static favorable-trading-window artifact")
    parser.add_argument("--key", required=True)
    parser.add_argument("--vol-wave", default=None)
    parser.add_argument("--lead-lag-wave", default=None)
    parser.add_argument("--fill-wave", default=None)
    parser.add_argument("--tercile", type=float, default=_DEFAULT_TERCILE)
    args = parser.parse_args()

    path = build_and_write(
        args.key,
        vol_wave_path=Path(args.vol_wave) if args.vol_wave else None,
        lead_lag_wave_path=Path(args.lead_lag_wave) if args.lead_lag_wave else None,
        fill_wave_path=Path(args.fill_wave) if args.fill_wave else None,
        tercile=args.tercile,
    )
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
