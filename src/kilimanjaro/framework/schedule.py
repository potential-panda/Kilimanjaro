"""Schedule filtering: block trades by JST hour, weekday, or specific dates.

Usage
-----
The ReturnCalc adds jst_hour, jst_weekday, jst_date columns to the trades
DataFrame before returning it. The runner then calls schedule.apply(trades)
to drop blocked rows and return counts of what was removed.

    from kilimanjaro.framework.schedule import ScheduleFilter, add_jst_columns

    df = add_jst_columns(df, ts_col="ts")
    schedule = ScheduleFilter(
        block_weekday_hours=[22, 23, 0, 1, 2, 3, 4, 5],
        block_weekend_hours=list(range(24)),
        block_gmo_maintenance=True,
    )
    df, counts = schedule.apply(df)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

_JST_OFFSET = 9 * 3600
_GMO_MAINTENANCE_SAT_HOURS: frozenset[int] = frozenset([9, 10, 11])


def add_jst_columns(
    df: pd.DataFrame,
    ts_col: str = "ts",
    date_ts_col: str | None = None,
) -> pd.DataFrame:
    """Add jst_hour, jst_weekday, jst_date columns derived from Unix timestamps.

    Parameters
    ----------
    ts_col : str
        Column to derive jst_hour and jst_weekday from.
    date_ts_col : str, optional
        Column to derive jst_date from (defaults to ts_col).
        Pass the entry ts when using exit_ts for hour/weekday (SAR style).
    """
    import numpy as np

    df = df.copy()

    # ts_col may contain NaN (e.g. exit_exec_ts for the last flip which has no
    # next flip). Only convert valid rows to int64; leave NaN rows as NaN.
    valid = df[ts_col].notna()
    df["jst_hour"] = np.nan
    df["jst_weekday"] = np.nan
    if valid.any():
        jst = (df.loc[valid, ts_col] + _JST_OFFSET).astype("int64")
        df.loc[valid, "jst_hour"] = (jst % 86400 // 3600).values
        df.loc[valid, "jst_weekday"] = pd.to_datetime(
            jst, unit="s", utc=True
        ).dt.dayofweek.values

    date_jst = (df[date_ts_col or ts_col] + _JST_OFFSET).astype("int64")
    df["jst_date"] = pd.to_datetime(date_jst, unit="s", utc=True).dt.date.astype(str)
    return df


@dataclass
class ScheduleFilter:
    """Immutable schedule filter applied to a trades DataFrame.

    All filtering operates on pre-computed jst_hour, jst_weekday, jst_date
    columns — call add_jst_columns() on the DataFrame before apply().

    Parameters
    ----------
    block_weekday_hours : list[int]
        JST hours (0–23) to block on Monday–Friday.
    block_weekend_hours : list[int]
        JST hours (0–23) to block on Saturday and Sunday.
    block_jst_dates : list[str]
        Specific YYYY-MM-DD dates (JST) to block entirely.
    block_gmo_maintenance : bool
        When True, additionally blocks Saturday JST 09–11 (GMO maintenance window).
    has_exit_col : str | None
        If set, only apply hour/weekday/GMO filters to rows where this column
        is not-null. Rows without an exit (last flip) are never hour-blocked.
    """

    block_weekday_hours: list[int] = field(default_factory=list)
    block_weekend_hours: list[int] = field(default_factory=list)
    block_jst_dates: list[str] = field(default_factory=list)
    block_gmo_maintenance: bool = False
    has_exit_col: str | None = None
    # Optional label; when set, run_grid writes it as the "schedule" column.
    name: str | None = None

    def apply(self, df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        """Apply all filters. Returns (filtered_df, counts).

        counts keys: n_date_blocked, n_weekday_blocked, n_sat_blocked
        """
        has_exit = (
            df[self.has_exit_col].notna()
            if self.has_exit_col and self.has_exit_col in df.columns
            else pd.Series(True, index=df.index)
        )

        n_date_blocked = 0
        if self.block_jst_dates:
            mask = df["jst_date"].isin(set(self.block_jst_dates))
            n_date_blocked = int(mask.sum())
            df = df[~mask].copy()
            has_exit = has_exit[~mask]

        n_weekday_blocked = 0
        if self.block_weekday_hours:
            mask = (
                has_exit
                & (df["jst_weekday"] <= 4)
                & df["jst_hour"].isin(set(self.block_weekday_hours))
            )
            n_weekday_blocked = int(mask.sum())
            df = df[~mask].copy()
            has_exit = has_exit[~mask]

        n_sat_blocked = 0
        if self.block_weekend_hours:
            mask = (
                has_exit
                & (df["jst_weekday"] >= 5)
                & df["jst_hour"].isin(set(self.block_weekend_hours))
            )
            n_sat_blocked += int(mask.sum())
            df = df[~mask].copy()
            has_exit = has_exit[~mask]

        if self.block_gmo_maintenance:
            mask = (
                has_exit
                & (df["jst_weekday"] == 5)
                & df["jst_hour"].isin(_GMO_MAINTENANCE_SAT_HOURS)
            )
            n_sat_blocked += int(mask.sum())
            df = df[~mask].copy()

        return df, {
            "n_date_blocked": n_date_blocked,
            "n_weekday_blocked": n_weekday_blocked,
            "n_sat_blocked": n_sat_blocked,
        }
