"""Registered provider adapters. These write only into a job's isolated lake."""

from __future__ import annotations

import shutil
from pathlib import Path

from kilimanjaro.data.catalog import inspect_partition, previous, relative_path
from kilimanjaro.research.identity import checksum


class DataUnavailable(RuntimeError):
    pass


class NeedsTooling(RuntimeError):
    pass


def dependencies(request: dict) -> list[dict]:
    dataset = request["dataset"]
    if dataset in {"Trade", "OrderBook"}:
        return []
    if dataset == "OBIFeature":
        source, columns, dates = (
            "BookState",
            ["ts", "vol_imb_k1", "vol_imb_k3", "vol_imb_k5", "vol_imb_k10"],
            [previous(request["date"]), request["date"]],
        )
    else:
        source = "Trade" if dataset == "TradeAggression" else "OrderBook"
        columns = (
            ["ts", "side", "price", "size"]
            if source == "Trade"
            else ["ts", "bid0_price", "bid0_qty", "ask0_price", "ask0_qty"]
        )
        dates = [request["date"]]
        if dataset in {"BookState", "BookChange", "TradeAggression"}:
            dates.insert(0, previous(request["date"]))
    return [
        {**request, "dataset": source, "date": day, "params": {}, "columns": columns}
        for day in dates
    ]


def link_lakes(parents: list[Path], destination: Path) -> None:
    """Link our own immutable snapshot files, never mutable shared-lake sources."""
    import os

    for parent in parents:
        lake = parent / "lake"
        if not lake.is_dir():
            continue
        for source in sorted(lake.rglob("*.parquet")):
            target = destination / source.relative_to(lake)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if checksum(target) != checksum(source):
                    raise ValueError(f"Conflicting dependency snapshots: {target}")
                continue
            try:
                os.link(source, target)
            except OSError:
                shutil.copyfile(source, target)


def derive(request: dict, root: Path) -> Path:
    """Use provider computation without altering its global settings or shared lake."""
    dataset, day = request["dataset"], request["date"]
    market_id = f"{request['exchange']}:{request['symbol']}"
    if dataset in {"MarketPrice", "Spread", "Volatility"}:
        from hase.derive import run

        run(root, dataset, market_id, day, **request.get("params", {}))
    elif dataset == "OBIFeature":
        from kilimanjaro.dataset.obi_feature_store import populate

        populate(request["exchange"], request["symbol"], day, day, data_root=root)
    elif dataset in {"BookState", "BookChange", "TradeAggression"}:
        from makalu.storage.parquet_store import ParquetStore
        from pipeline.framework import Pipeline
        from pipeline.order_book_derived_steps import (
            PopulateBookChangeStep,
            PopulateBookStateStep,
        )
        from pipeline.stages import OrderBookDerivedStage, TradeDerivedStage
        from pipeline.trade_derived_steps import PopulateAggressionStep
        from qate.core.model import Market
        from qate.util.dt_range import DtRange

        market = Market.from_str(market_id)
        if dataset == "TradeAggression":
            stage = TradeDerivedStage(market).step(
                PopulateAggressionStep(windows_sec=[1, 3, 5, 10, 30, 60])
            )
        elif dataset == "BookChange":
            stage = OrderBookDerivedStage(market).step(
                PopulateBookChangeStep(k_levels=[1, 3, 5, 10])
            )
        else:
            stage = OrderBookDerivedStage(market).step(
                PopulateBookStateStep(
                    volume_imbalance_k=[1, 3, 5, 10],
                    weighted_imbalance_k=[1, 3, 5, 10],
                    depth_imbalance_rate=[0.0001, 0.0002, 0.0005, 0.001],
                    microprice_k=[1, 3, 5, 10],
                )
            )
        Pipeline(ParquetStore(str(root))).stage(stage).process(
            DtRange.from_strings(day, day)
        )
    else:
        raise NeedsTooling(f"No registered derivation for {dataset}")
    inspection = inspect_partition(root, request)
    if inspection["status"] != "ready":
        raise DataUnavailable(f"Provider output is not usable: {inspection}")
    return root / relative_path(request)
