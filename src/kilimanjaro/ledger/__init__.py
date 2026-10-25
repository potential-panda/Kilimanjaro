"""Research ledger — the append-only event log + duplicate-detection search.

Moved into the package (was tools/ledger.py + tools/grid_ledger.py) so library
code imports it normally instead of mutating sys.path. `core` is the general
event log; `grid` records a grid-scan run as a scan_attached event. The CLI is
`cli.py ledger`.
"""

from kilimanjaro.ledger.core import (
    append_event,
    current_status,
    load_events,
    load_hypotheses,
    next_event_id,
    search,
)
from kilimanjaro.ledger.grid import record_grid_scan

__all__ = [
    "append_event",
    "current_status",
    "load_events",
    "load_hypotheses",
    "next_event_id",
    "record_grid_scan",
    "search",
]
