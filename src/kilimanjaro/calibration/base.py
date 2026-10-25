"""FactorCalib: one calibratable factor. Reads the SAME per-second series from both
sources over one window and exact-matches them:

  offline (Kilimanjaro): eval the factor's expression over the Makalu feature frame
                         (parquet) — exactly what the scan feeds the composite.
  online  (Himalaya):    the factor value the qate strategy emitted each second via
                         add_metric(), stored in InfluxDB (measurement TRADING).

Each factor lives in its own module under calibration/factors/ holding a FactorCalib;
registry.py lists them. The [match, fix] loop: run calibrate(), read pct_match, if it is
below target inspect .mismatches, fix the online engine (aquila) or the offline recipe,
repeat until ≈100%.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from kilimanjaro.calibration.compare import CalibResult, compare

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")
_TRADING_MEASUREMENT = "TRADING"  # qate create_trading_metric -> Measurement.TRADING

# Warmup the offline rolling windows to the SAME converged state Himalaya has at the
# window start. The composite's slowest ops are the 3600 s basis + 1800 s z-scores;
# without prior history the scan's first ~hour is under-converged and diverges from
# aquila (which warms its engine from the data loaded before the run's from-dt). 2 days
# is comfortably more than 3600 s. Extra pre-window offline seconds are harmless — the
# compare intersects offline/online indices and drops the offline-only warmup rows.
_WARMUP_DAYS = 2


def _shift_date(date_str: str, days: int) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(date_str) - timedelta(days=days)).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class FactorCalib:
    name: str  # canonical factor name; also the InfluxDB field & offline label
    offline_expr: (
        str  # expression over the feature frame, e.g. "zscore(a_mid/b_mid, 900)"
    )
    online_field: str = ""  # InfluxDB TRADING field; defaults to `name`
    status: str = "pending"  # "pending" | "calibrated"
    tol: float = 1e-4
    note: str = ""

    @property
    def field(self) -> str:
        return self.online_field or self.name

    # ---- offline: Kilimanjaro / Makalu parquet --------------------------------
    def offline_series(
        self,
        markets: dict,
        start: str,
        end: str,
        data_root: Path = _DEFAULT_DATA_ROOT,
        warmup_days: int = _WARMUP_DAYS,
    ) -> pd.Series:
        """Per-second factor series from the feature frame (index = integer second).

        Loads from `start - warmup_days` so the rolling windows converge before the
        compared window; the extra warmup seconds are dropped by the compare intersect."""
        from kilimanjaro.signal.expression import ExpressionSignal, eval_expr

        sig = ExpressionSignal.create(
            markets=markets,
            expr=self.offline_expr,
            threshold=0.0,
            min_age=None,
            max_age=None,
            invert=False,
            label=self.name,
        )
        frame = sig.load_data(_shift_date(start, warmup_days), end, data_root)
        ts = frame["ts"].to_numpy() if "ts" in frame.columns else frame.index.to_numpy()
        vals = np.asarray(eval_expr(self.offline_expr, frame), dtype=float)
        s = pd.Series(vals, index=np.floor(ts).astype(np.int64))
        return s.groupby(level=0).last()

    # ---- online: Himalaya / InfluxDB ------------------------------------------
    def online_series(self, bucket: str, start: str, end: str) -> pd.Series:
        """Per-second factor series from InfluxDB (measurement TRADING, field=self.field)."""
        from kilimanjaro.framework.verify import (
            build_flux,
            influx_client,
            influx_range_jst,
            query_df,
        )

        rfc_start, rfc_stop = influx_range_jst(start, end)
        flux = build_flux(
            bucket=bucket,
            measurement=_TRADING_MEASUREMENT,
            fields=[self.field],
            start=rfc_start,
            stop=rfc_stop,
        )
        with influx_client() as c:
            df = query_df(c.query_api(), flux)
        if df.empty or self.field not in df.columns:
            return pd.Series(dtype=float)
        sec = df["_time"].apply(lambda t: int(t.timestamp()))
        s = pd.Series(df[self.field].to_numpy(dtype=float), index=sec.to_numpy())
        return s.groupby(level=0).last()

    # ---- match -----------------------------------------------------------------
    def calibrate(
        self,
        markets: dict,
        bucket: str,
        start: str,
        end: str,
        data_root: Path = _DEFAULT_DATA_ROOT,
        tol: float | None = None,
    ) -> CalibResult:
        off = self.offline_series(markets, start, end, data_root)
        on = self.online_series(bucket, start, end)
        return compare(self.name, off, on, tol=self.tol if tol is None else tol)
