"""MarketPrice glob path helper shared by return_calc implementations."""

from __future__ import annotations

from pathlib import Path


def mp_glob(exchange: str, symbol: str, execution_size: str, data_root: Path) -> str:
    return str(
        data_root / "silver" / "dataset=MarketPrice"
        / f"exchange={exchange}" / f"symbol={symbol}"
        / f"execution_size={execution_size}"
        / "date=*" / "*.parquet"
    )
