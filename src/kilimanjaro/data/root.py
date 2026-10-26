"""Resolve the Hase/Komachi-managed market-data root.

The parquet layout remains stable, but the root is owned by Komachi and shared
with Hase.  Keeping that decision here prevents callers from baking a former
Makalu path into new code.
"""

from __future__ import annotations

from pathlib import Path


def data_root(override: str | Path | None = None) -> Path:
    """Resolve at call time; an explicit research/test root never imports Hase."""
    if override is not None:
        return Path(override).expanduser()
    try:
        from hase.bronze import data_root as hase_data_root
    except ImportError as exc:  # pragma: no cover - installation error
        raise RuntimeError("Hase is required to resolve the market-data root") from exc
    return hase_data_root()
