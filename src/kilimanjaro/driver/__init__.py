"""Kilimanjaro driver: the autonomous scanner loop (steps 1-4).

This package is the *driver* of the research machine, not part of its core.
`kilimanjaro.framework` knows how to run and evaluate one grid; the driver
sequences many of them unattended:

    generate/queue spec -> validate -> dedup -> scan -> verdict -> ledger
    -> park survivors for the human gate.

steps 5-7 (build qate strategy, Himalaya backtest, prod) stay human-gated and
live outside this loop. The driver never promotes a hypothesis; it only attaches
evidence and moves a hypothesis to scan-passed / scan-failed, drafting a review
packet for a human when something survives.

See doc/requirements/agent-loop-design.md for the state machine this implements.
"""

from kilimanjaro.driver.loop import process_spec, run_once
from kilimanjaro.driver.policy import (
    Outcome,
    evaluate,
    evaluate_episode,
    select_candidates,
)
from kilimanjaro.driver.validate import validate_scan_spec

__all__ = [
    "run_once",
    "process_spec",
    "evaluate",
    "evaluate_episode",
    "select_candidates",
    "Outcome",
    "validate_scan_spec",
]
