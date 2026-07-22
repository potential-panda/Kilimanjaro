"""Companion hypothesis-contract entries for generated scan specs.

`generate` writes *scan* specs to `cycle/12_specs/queue` — the loop's working
format. The hypothesis registry (`cycle/11_hypotheses`) is a different, richer
format: the durable human-readable memory of every idea tried, and the outer
collection that `ledger.search` and the viewer's Hypotheses tab iterate over. A
hypothesis with ledger events but no registry entry is invisible to both.

The two formats are not interchangeable, so nothing is *moved*: this module
derives a contract-shaped companion entry from a scan spec (plus the ledger, for
status and decisions) and writes it alongside. Every field is derived from the
spec, the ledger, or the fixed execution-cost policy — see `_falsification_tests`
for the one place where that needs saying out loud.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA = _ROOT / "cycle" / "01_schemas" / "hypothesis_spec.schema.json"
_REGISTRY_DIR = _ROOT / "cycle" / "11_hypotheses"

# The generator's family names are its own vocabulary; the contract has a fixed
# enum. Map onto the closest contract term and keep the generator's own name in
# `family_label` so neither is lost.
_FAMILY_MAP = {
    "momentum": ("custom", "Price momentum"),
    "orderflow": ("trade_aggression_burst", "Order flow"),
    "obi": ("order_book_imbalance", "Order-book imbalance"),
    "reversion": ("liquidity_shock_mean_reversion", "Mean reversion"),
    "basis": ("lead_lag", "Cross-market basis"),
}

# Which raw dataset each feature-frame variable is read from.
_VAR_DATASETS = {
    "mid": ("silver", "MarketPrice"),
    "spread": ("silver", "MarketPrice"),
    "obi": ("feature", "OBIFeature"),
    "aggression": ("feature", "TradeAggression"),
}


def _datasets_for(expr_text: str) -> list[dict]:
    """Datasets an expression actually reads, by variable suffix."""
    found = []
    for var, (layer, dataset) in _VAR_DATASETS.items():
        if re.search(rf"\b[ab]?_?{var}\b", expr_text):
            entry = {"layer": layer, "dataset": dataset}
            if entry not in found:
                found.append(entry)
    return found or [{"layer": "silver", "dataset": "MarketPrice"}]


def _falsification_tests(spec: dict) -> list[dict]:
    """The gates the scan pipeline actually applies to this spec.

    These are not aspirational tests written after the fact — they are the
    checks `run_grid_split` runs on every combo, so naming them here is a
    description of what the scan did, not a claim about what someone intended.
    """
    tests = [
        {
            "name": "rank-instability-across-split",
            "expected_failure_meaning": (
                "top-decile train combos fall out of the holdout top decile — "
                "selection noise, not signal"
            ),
        },
        {
            "name": "multiple-testing-fdr",
            "expected_failure_meaning": (
                "no combo survives Benjamini-Hochberg across the grid — the best "
                "result is what a grid of this size yields by chance"
            ),
        },
        {
            "name": "cost-buffer",
            "expected_failure_meaning": (
                "edge does not clear the round-trip cost floor (bid/ask execution "
                "plus 1.0 bps latency slippage) — gross-only edge"
            ),
        },
    ]
    if spec.get("family") == "basis":
        tests.append(
            {
                "name": "regime-sign-flip",
                "expected_failure_meaning": (
                    "signal direction inverts between train and holdout — regime "
                    "whipsaw rather than a persistent basis relationship"
                ),
            }
        )
    return tests


def _status_and_decisions(hyp_id: str, events: list[dict]) -> tuple[str, list[dict]]:
    """Current status + decision trail, read from the ledger."""
    decisions = []
    for e in events:
        if e.get("event_type") != "status_transition":
            continue
        to = (e.get("details") or {}).get("to_status") or e.get("to_status")
        if not to:
            continue
        reason = (e.get("details") or {}).get("verdict_text") or (
            e.get("details") or {}
        ).get("headline")
        d = {
            "at": e.get("created_at"),
            "actor": e.get("actor_type", "agent"),
            "decision": to,
        }
        if reason:
            d["reason"] = reason
        decisions.append(d)
    if not decisions:
        decisions = [
            {
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "actor": "agent",
                "decision": "draft",
                "reason": "proposed by the generator; no ledger activity yet",
            }
        ]
    return decisions[-1]["decision"], decisions


def build_entry(
    spec: dict,
    *,
    events: list[dict] | None = None,
    signature: str | None = None,
    window: tuple[str, str] | None = None,
) -> dict:
    """Derive a hypothesis-contract entry from a generated scan spec."""
    hyp_id = spec["hypothesis_id"]
    events = events or []
    family_key = spec.get("family", "custom")
    family, family_label = _FAMILY_MAP.get(family_key, ("custom", family_key))

    target = spec["target"]
    markets = spec.get("markets") or {"a": target}
    horizons = spec.get("horizons") or [120]
    exprs = spec.get("expressions") or []
    expr_text = " ".join(e.get("expr", "") for e in exprs)

    status, decisions = _status_and_decisions(hyp_id, events)
    created_at = decisions[0]["at"]

    is_cross_market = any(
        m.get("exchange") != target["exchange"] or m.get("symbol") != target["symbol"]
        for m in markets.values()
    )
    cross_market_rationale = None
    if is_cross_market:
        srcs = ", ".join(
            f"{m['exchange']}/{m['symbol']}"
            for m in markets.values()
            if m.get("exchange") != target["exchange"]
            or m.get("symbol") != target["symbol"]
        )
        cross_market_rationale = (
            f"The feature is computed on {srcs} and predicts "
            f"{target['exchange']}/{target['symbol']}. The premise is that the "
            f"venues price the same underlying, so a dislocation in their ratio "
            f"mean-reverts; the leading venue's move is informative because it is "
            f"the more liquid price discovery site, not merely correlated."
        )

    dup = {
        "searched_at": created_at,
        "query_terms": [family_key, target["symbol"]]
        + [e.get("label", "") for e in exprs if e.get("label")],
        "possible_duplicates": [],
        "conclusion": "new",
    }
    if signature:
        dup["query_terms"].append(f"signature:{signature}")

    return {
        "schema_version": 1,
        "hypothesis": {
            "id": hyp_id,
            "parent_id": None,
            "title": f"{family_label} on {target['exchange']}/{target['symbol']}",
            "status": status,
            "family": family,
            "family_label": family_label,
            "created_at": created_at,
            "owner": "agent",
            "expected_edge_mechanism": spec.get("mechanism")
            or f"{family_label} signal on {target['symbol']}",
            "duplicate_search": dup,
            "data_requirements": {
                "markets": [
                    {"exchange": m["exchange"], "symbol": m["symbol"]}
                    for m in markets.values()
                ],
                "datasets": _datasets_for(expr_text),
                "minimum_distinct_days": 200,
                # The generator is data-gated — it only proposes markets that
                # already have data, so a shortfall means the window is short.
                "unavailable_data_policy": "needs-more-data",
                "research_metadata": {
                    "generated_by": "driver.generate",
                    "scan_window": list(window) if window else None,
                },
            },
            "feature": {
                "sql_expression": "; ".join(
                    f"{e.get('label', '?')}: {e.get('expr', '')}" for e in exprs
                ),
                "source_datasets": [
                    {
                        "layer": ds["layer"],
                        "dataset": ds["dataset"],
                        "exchange": m["exchange"],
                        "symbol": m["symbol"],
                        "alias": alias,
                    }
                    for alias, m in markets.items()
                    for ds in _datasets_for(expr_text)
                ],
                "primary_timestamp_column": "ts",
                "point_in_time_rule": (
                    "All ops causal (test-enforced, kilimanjaro/op/series.py); "
                    "frame values ffilled at-or-before ts."
                ),
            },
            "target": {
                "type": "next_return",
                "primary_horizon_seconds": horizons[len(horizons) // 2],
                "comparison_horizons_seconds": list(horizons),
                "source_dataset": {
                    "layer": "silver",
                    "dataset": "MarketPrice",
                    "exchange": target["exchange"],
                    "symbol": target["symbol"],
                },
                "timestamp_cutoff_rule": (
                    "target window starts strictly after the signal timestamp"
                ),
            },
            "execution_assumptions": {
                # Fixed policy, framework/costs.py: bid/ask execution already pays
                # the spread; the only added cost is latency slippage.
                "order_type": "market",
                "fees_bps": 0,
                "slippage_bps": 1.0,
                "liquidity_capacity_assumption": (
                    f"execution size {target.get('execution_size', 'n/a')}"
                ),
            },
            "falsification_tests": _falsification_tests(spec),
            "scan_plan": {
                "primary_horizon_seconds": horizons[len(horizons) // 2],
                "threshold_grid": list(spec.get("thresholds") or []),
                "significance_method": "block_bootstrap_by_day",
                "selection_rule": (
                    "rank stability across a train/holdout split, then "
                    "Benjamini-Hochberg across the grid"
                ),
            },
            "backtest_handoff_intent": {
                "potential_panda_core_strategy_pattern": (
                    "qate.trading.strategy variant, threshold entry, "
                    f"{spec.get('return_calc', 'fixed_horizon')} exit"
                ),
                "himalaya_config_notes": (
                    "only if a rank-stable combo survives with meaningful "
                    "absolute holdout strength"
                ),
            },
            "decisions": decisions,
            "cross_market_rationale": cross_market_rationale,
        },
    }


def validate_entry(entry: dict) -> None:
    """Raise if the entry does not satisfy the hypothesis contract."""
    import jsonschema

    jsonschema.validate(entry, json.loads(_SCHEMA.read_text()))


def write_entry(entry: dict, registry_dir: Path = _REGISTRY_DIR) -> Path:
    """Validate and write one registry entry. Never overwrites an existing file."""
    registry_dir = Path(registry_dir)
    registry_dir.mkdir(parents=True, exist_ok=True)
    validate_entry(entry)
    path = registry_dir / f"{entry['hypothesis']['id']}.yaml"
    if path.exists():
        return path
    path.write_text(yaml.safe_dump(entry, sort_keys=False, allow_unicode=True))
    return path
