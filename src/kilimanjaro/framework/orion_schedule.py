"""Trading schedule = orion's schedule module, as the single source of truth.

The scan and Himalaya MUST apply the same schedule or their trade sets diverge.
We measured an 18% trade-count gap (and a ~2x PnL gap) on BTC because the scan's
hand-rolled "active" schedule blocked Sunday while orion's "default_active"
trades it. Rather than maintain a parallel schedule definition in kilimanjaro,
drive the scan from ``orion.schedule.get_schedule(version).check()`` directly —
so the entry filter is, by construction, the exact one Himalaya runs.

orion (and its ``qate`` dependency) live in sibling repos; we add them to
sys.path on import. Every schedule version the scan uses is (weekday, hour)-
deterministic, so we materialize orion's ``check()`` into a 7x24 table once —
fast, and still orion's own logic as the source of truth.
"""

from __future__ import annotations

import datetime as _dt
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

_ORION_SRC = Path.home() / "workspace" / "potential-panda-orion" / "src"
_QATE_SRC = Path.home() / "workspace" / "potential-panda-core" / "src"
for _p in (_ORION_SRC, _QATE_SRC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.append(str(_p))

_JST_OFFSET = 9 * 3600
_REF_MONDAY = _dt.datetime(2026, 2, 2)  # a Monday: probe every (weekday, hour)


@lru_cache(maxsize=None)
def _allowed_table(version: str) -> tuple[bool, ...]:
    """orion.check() materialized as a flat 7*24 bool tuple (weekday-major).

    Import of orion is deferred to here, so merely importing this module never
    hard-fails in an environment without the sibling repos.
    """
    from orion.schedule import get_schedule
    from qate.core.model import TradingMode

    sch = get_schedule(version)
    return tuple(
        sch.check((_REF_MONDAY + _dt.timedelta(days=wd, hours=h)).timestamp())
        == TradingMode.DEFAULT
        for wd in range(7)
        for h in range(24)
    )


def entry_allowed(version: str, ts_seconds) -> np.ndarray:
    """Bool mask: orion permits ENTRY (check == DEFAULT) at each unix ts."""
    lut = np.array(_allowed_table(version), dtype=bool)
    ts = np.asarray(ts_seconds, dtype="int64")
    jst = pd.to_datetime(ts + _JST_OFFSET, unit="s", utc=True)
    return lut[jst.dayofweek.to_numpy() * 24 + jst.hour.to_numpy()]


@dataclass
class OrionScheduleFilter:
    """ScheduleFilter-compatible filter driven by orion's schedule module.

    Keeps trades whose ENTRY timestamp orion permits (check == DEFAULT) — the
    exact rule Himalaya applies, so scan and backtest agree by construction.
    ``version`` is an orion schedule version (default_active, default_inactive,
    xrp_active, weekday, weekend, us_on, us_off, nofilter, ...).

    Duck-typed against ScheduleFilter: exposes ``.name`` and ``.apply()``.
    """

    version: str
    ts_col: str = "ts"  # entry decision time (orion checks the entry ts)
    has_exit_col: str | None = None

    @property
    def name(self) -> str:
        return self.version

    @property
    def params(self) -> dict:
        return {"schedule": self.version}

    def apply(self, df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        if len(df) == 0:
            return df, {"n_schedule_blocked": 0}
        allowed = entry_allowed(self.version, df[self.ts_col].to_numpy())
        if self.has_exit_col and self.has_exit_col in df.columns:
            has_exit = df[self.has_exit_col].notna().to_numpy()
        else:
            has_exit = np.ones(len(df), dtype=bool)
        block = has_exit & ~allowed
        return df[~block].copy(), {"n_schedule_blocked": int(block.sum())}
