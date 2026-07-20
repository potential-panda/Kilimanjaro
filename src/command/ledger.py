"""`cli.py ledger` — append to / search the research ledger.

Thin CLI over kilimanjaro.ledger.core (was tools/ledger.py). The append/search
argument definitions live in core.register_cli so there is one source.
"""

from __future__ import annotations

import argparse

from kilimanjaro.ledger import core


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("ledger", help="Research ledger: append / search events")
    lsub = p.add_subparsers(dest="ledger_cmd", required=True)
    core.register_cli(lsub)
