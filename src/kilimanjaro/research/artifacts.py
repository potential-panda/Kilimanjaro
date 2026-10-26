"""Atomic, immutable research artifacts and checksum-verified publication."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .identity import canonical, checksum


def write_json(path: Path, value) -> None:
    path = Path(path)
    payload = canonical(value) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text() != payload:
            raise ValueError(f"Immutable artifact conflict: {path}")
        return
    fd, name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # No-clobber publication even if another writer races us.
        try:
            os.link(name, path)
        except FileExistsError:
            if path.read_text() != payload:
                raise ValueError(f"Immutable artifact conflict: {path}") from None
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def manifest(folder: Path) -> dict:
    return {
        str(p.relative_to(folder)): checksum(p)
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.name != "manifest.json"
    }


def verify(folder: Path, files: dict) -> None:
    if not files:
        raise ValueError("No output artifacts")
    for relative, expected in files.items():
        path = folder / relative
        if path.resolve().is_relative_to(folder.resolve()) is False:
            raise ValueError("Artifact escapes its directory")
        if not path.is_file() or checksum(path) != expected:
            raise ValueError(f"Missing or changed artifact: {relative}")


def read_json(path: Path):
    return json.loads(Path(path).read_text())
