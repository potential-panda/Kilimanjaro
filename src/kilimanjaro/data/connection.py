"""DuckDB resource settings scoped to research worker processes."""

import os

import duckdb


def connect():
    limit = os.environ.get("KILIMANJARO_QUERY_MEMORY_MB")
    if limit is None:
        return duckdb.connect()
    return duckdb.connect(config={"threads": 1, "memory_limit": f"{int(limit)}MB"})
