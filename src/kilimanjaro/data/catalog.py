"""Verified partition inventory for research; no writes during inspection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from kilimanjaro.research.contracts import identifier, instant
from kilimanjaro.research.identity import checksum

JST = ZoneInfo("Asia/Tokyo")
LAYERS = {
    "Trade": "bronze",
    "OrderBook": "bronze",
    "MarketPrice": "silver",
    "BookState": "silver",
    "BookChange": "silver",
    "TradeAggression": "silver",
    "OBIFeature": "feature",
    "Spread": "gold",
    "Volatility": "gold",
}


def days(start: float, end: float) -> list[str]:
    first = datetime.fromtimestamp(start, JST).date()
    last = datetime.fromtimestamp(end - 0.000001, JST).date()
    return [
        (first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)
    ]


def previous(day: str) -> str:
    return (datetime.fromisoformat(day).date() - timedelta(days=1)).isoformat()


def relative_path(request: dict) -> Path:
    dataset = request["dataset"]
    if dataset not in LAYERS:
        raise ValueError(f"No registered provider for {dataset}")
    exchange, symbol = identifier(request["exchange"]), identifier(request["symbol"])
    day = datetime.fromisoformat(request["date"]).date().isoformat()
    params = request.get("params", {})
    if dataset in {"MarketPrice", "Spread", "Volatility"}:
        from hase.derive import plan
        from hase.layout import derived_path

        partition, _ = plan(dataset, **params)
        return derived_path(Path("."), dataset, f"{exchange}:{symbol}", day, partition)
    return (
        Path(LAYERS[dataset])
        / f"dataset={dataset}"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
        / f"date={day}"
        / "data.parquet"
    )


def inspect_partition(root: Path, request: dict, *, hash_content=True) -> dict:
    import pyarrow.parquet as pq

    relative = relative_path(request)
    path = root / relative
    result = {"request": request, "relative_path": str(relative)}
    if not path.is_file():
        status = (
            "structural_gap"
            if request["dataset"] in {"Trade", "OrderBook"}
            and "2026-04-02" <= request["date"] <= "2026-04-09"
            else "missing"
        )
        return {**result, "status": status}
    try:
        parquet = pq.ParquetFile(path)
        missing = sorted(set(request["columns"]) - set(parquet.schema_arrow.names))
        if missing:
            return {**result, "status": "schema_mismatch", "missing_columns": missing}
        if parquet.metadata.num_rows == 0:
            return {**result, "status": "empty", "rows": 0}
        ts_index = parquet.schema_arrow.get_field_index("ts")
        bounds = []
        for i in range(parquet.metadata.num_row_groups):
            stats = parquet.metadata.row_group(i).column(ts_index).statistics
            if stats and stats.has_min_max:
                bounds.extend((float(stats.min), float(stats.max)))
        day_start = (
            datetime.fromisoformat(request["date"]).replace(tzinfo=JST).timestamp()
        )
        if bounds and (min(bounds) < day_start or max(bounds) >= day_start + 86400):
            return {
                **result,
                "status": "invalid_timestamp",
                "min_ts": min(bounds),
                "max_ts": max(bounds),
            }
        if datetime.now(UTC).timestamp() < day_start + 86400:
            return {**result, "status": "incomplete_day"}
        stat = path.stat()
        before = (stat.st_size, stat.st_mtime_ns)
        digest = checksum(path) if hash_content else None
        after = path.stat()
        if before != (after.st_size, after.st_mtime_ns):
            return {**result, "status": "changing"}
        return {
            **result,
            "status": "ready",
            "rows": parquet.metadata.num_rows,
            "bytes": stat.st_size,
            "checksum": digest,
            "columns": sorted(parquet.schema_arrow.names),
            "min_ts": min(bounds) if bounds else None,
            "max_ts": max(bounds) if bounds else None,
        }
    except Exception as exc:  # noqa: BLE001 - provider-specific corruption exceptions
        # External parquet readers expose version-specific corruption exceptions.
        return {**result, "status": "invalid", "error": f"{type(exc).__name__}: {exc}"}


def feature_requests(spec: dict) -> list[dict]:
    from kilimanjaro.data.feature_frame import _obi_source_col, _route
    from kilimanjaro.signal.expression import validate_expr

    grouped = {}
    columns = {
        c for feature in spec["features"] for c in validate_expr(feature["expr"])
    }
    columns.update(f"{spec['target']}_{c}" for c in ("mid", "bid", "ask"))
    for column in sorted(columns):
        alias, feature = column.split("_", 1)
        route = next(k for k, v in _route([feature]).items() if v)
        dataset = {
            "mp": "MarketPrice",
            "obi": "OBIFeature",
            "ta": "TradeAggression",
            "bs": "BookState",
            "bc": "BookChange",
        }[route]
        source = (
            feature
            if route == "mp"
            else (_obi_source_col(feature) if route == "obi" else feature[3:])
        )
        grouped.setdefault((alias, dataset), set()).add(source)
    start = instant(spec["windows"]["train"]["start"]) - spec["warmup_seconds"]
    end = instant(spec["windows"]["validation"]["end"])
    requests = []
    for (alias, dataset), required in sorted(grouped.items()):
        market = spec["markets"][alias]
        params = (
            {"execution_size": float(market["execution_size"])}
            if dataset == "MarketPrice"
            else {}
        )
        params.update(spec.get("provider_params", {}).get(dataset, {}))
        for day in days(start, end):
            requests.append(
                {
                    "dataset": dataset,
                    "exchange": market["exchange"],
                    "symbol": market["symbol"],
                    "date": day,
                    "params": params,
                    "columns": sorted({"ts", *required}),
                }
            )
    return requests
