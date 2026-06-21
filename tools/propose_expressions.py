#!/usr/bin/env python3
"""Deterministic expression proposer for early-stage hypothesis input.

This is intentionally not a model and not a strategy generator. It creates
AlphaGPT-style symbolic feature expressions, then wraps them in Kilimanjaro
hypothesis specs so the existing validator/scanner/evaluator/ledger path can
act as the reality filter.

v1 scope:
  - bronze OrderBook only, matching tools/scanner.py's current implemented
    data shape;
  - scanner-safe scalar SQL expressions only;
  - market sourcing follows the existing BINANCE-leads-GMO default for
    non-local mechanics families;
  - generated files are draft specs, never scan-approved artifacts.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from markets import default_slippage_bps, markets  # noqa: E402
from validate_spec import validate  # noqa: E402

_ROOT = Path(__file__).parent.parent
_DEFAULT_SPECS_DIR = _ROOT / "specs"

_KNOWN_LEADERS = {
    ("GMO", "BTC_JPY"): ("BINANCE", "BTC_USDT"),
    ("GMO", "ETH_JPY"): ("BINANCE", "ETH_USDT"),
    ("GMO", "XRP_JPY"): ("BINANCE", "XRP_USDT"),
}

_FAMILY_LABELS = {
    "order_book_imbalance": "Order book imbalance",
    "weighted_imbalance_microprice": "Weighted imbalance and microprice",
    "spread_regime": "Spread widening/narrowing regimes",
    "depth_pull_refill_asymmetry": "Depth pull/refill asymmetry",
}


@dataclass(frozen=True)
class ExpressionTemplate:
    slug: str
    family: str
    sql_expression: str
    required_columns: tuple[str, ...]
    mechanism: str
    rationale: str
    falsification_name: str
    falsification_meaning: str


_IMBALANCE = "(bid0_qty - ask0_qty) / nullif(bid0_qty + ask0_qty, 0)"
_MID = "((bid0_price + ask0_price) / 2.0)"
_SPREAD = "(ask0_price - bid0_price)"
_MICROPRICE = "((ask0_price * bid0_qty + bid0_price * ask0_qty) / nullif(bid0_qty + ask0_qty, 0))"

_TEMPLATES = (
    ExpressionTemplate(
        slug="top_book_imbalance",
        family="order_book_imbalance",
        sql_expression=_IMBALANCE,
        required_columns=("bid0_qty", "ask0_qty"),
        mechanism=(
            "Top-of-book bid/ask quantity imbalance is a compact proxy for "
            "near-term buying versus selling pressure. If the market has not "
            "fully repriced that pressure, the sign and magnitude should align "
            "with future mid-price return."
        ),
        rationale=(
            "A persistent imbalance can force liquidity takers or market makers "
            "to clear the thinner side first, moving the mid price before the "
            "target market fully adjusts."
        ),
        falsification_name="imbalance-effect-is-one-day-only",
        falsification_meaning="effect disappears once tested across multiple distinct days",
    ),
    ExpressionTemplate(
        slug="microprice_deviation",
        family="weighted_imbalance_microprice",
        sql_expression=f"({_MICROPRICE} - {_MID}) / nullif({_SPREAD}, 0)",
        required_columns=("bid0_price", "bid0_qty", "ask0_price", "ask0_qty"),
        mechanism=(
            "The size-weighted microprice moves toward the side with less "
            "available top-of-book depth. A microprice displaced from the mid "
            "can indicate the next short-horizon mid-price adjustment."
        ),
        rationale=(
            "When displayed depth is asymmetric, the fair execution-aware price "
            "is closer to the thinner side than the simple mid; future returns "
            "should partially close that gap if the signal is real."
        ),
        falsification_name="microprice-gap-does-not-rank-returns",
        falsification_meaning="return buckets are not monotonic by microprice displacement",
    ),
    ExpressionTemplate(
        slug="spread_scaled_imbalance",
        family="spread_regime",
        sql_expression=f"{_IMBALANCE} / greatest({_SPREAD} / nullif({_MID}, 0), 0.000001)",
        required_columns=("bid0_price", "bid0_qty", "ask0_price", "ask0_qty"),
        mechanism=(
            "The same displayed imbalance is more economically meaningful when "
            "the spread is tight enough for the market to clear costs. Scaling "
            "imbalance by relative spread tests whether pressure matters most "
            "when execution friction is lower."
        ),
        rationale=(
            "Raw imbalance can be untradeable in wide-spread periods. A spread "
            "scaled expression should emphasize pressure that has a plausible "
            "path through fees, spread, and slippage."
        ),
        falsification_name="spread-scaling-only-selects-noisy-tight-spread-rows",
        falsification_meaning="edge vanishes after effective-sample and cost-buffer checks",
    ),
    ExpressionTemplate(
        slug="imbalance_vs_recent_past",
        family="depth_pull_refill_asymmetry",
        sql_expression=(
            f"{_IMBALANCE} - avg({_IMBALANCE}) OVER "
            "(ORDER BY ts ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING)"
        ),
        required_columns=("ts", "bid0_qty", "ask0_qty"),
        mechanism=(
            "A sudden change in top-of-book imbalance versus its recent past can "
            "capture pull/refill behavior before the next mid-price movement."
        ),
        rationale=(
            "Depth changes are more informative when measured as a local shock "
            "rather than as a static level; this expression tests whether recent "
            "book pressure acceleration predicts the target return."
        ),
        falsification_name="imbalance-shock-is-not-stable-across-neighbor-horizons",
        falsification_meaning="only one horizon cell works, indicating likely post-hoc noise",
    ),
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _target_markets(target_exchange: str | None, target_symbol: str | None) -> list[dict]:
    candidates = markets()
    if target_exchange:
        candidates = [m for m in candidates if m["exchange"] == target_exchange]
    if target_symbol:
        candidates = [m for m in candidates if m["symbol"] == target_symbol]
    if not candidates:
        raise ValueError("no target markets matched the requested filters")
    return candidates


def _source_market(target: dict, family: str) -> tuple[str, str, str | None]:
    target_key = (target["exchange"], target["symbol"])
    leader = _KNOWN_LEADERS.get(target_key)
    if leader is None:
        return target["exchange"], target["symbol"], None

    leader_exchange, leader_symbol = leader
    rationale = (
        f"Feature is computed on {leader_exchange}:{leader_symbol}; target return is on "
        f"{target['exchange']}:{target['symbol']}. This follows Kilimanjaro's default "
        "lead-lag sourcing rule for this target: the leader market's order-book state "
        "is treated as the informative source, while the target market is where the "
        "return and execution costs are evaluated."
    )
    return leader_exchange, leader_symbol, rationale


def _order_type_for_spec(registry_market: dict) -> str:
    return "limit" if registry_market["order_type"] == "limit_only" else "market"


def _next_sequence(specs_dir: Path, date_str: str) -> int:
    pattern = re.compile(rf"^hyp-{re.escape(date_str)}-(\d{{3}})(?:-v\d{{2}})?\.ya?ml$")
    max_seen = 0
    if specs_dir.exists():
        for path in specs_dir.iterdir():
            match = pattern.match(path.name)
            if match:
                max_seen = max(max_seen, int(match.group(1)))
    return max_seen + 1


def _query_terms(template: ExpressionTemplate, source_exchange: str, source_symbol: str, target: dict) -> list[str]:
    return [
        template.family,
        template.slug,
        source_exchange,
        source_symbol,
        target["exchange"],
        target["symbol"],
    ]


def build_spec(
    hypothesis_id: str,
    template: ExpressionTemplate,
    target: dict,
    created_at: str,
    primary_horizon_seconds: int = 120,
) -> dict:
    source_exchange, source_symbol, cross_market_rationale = _source_market(target, template.family)
    comparison_horizons = sorted({30, primary_horizon_seconds, 300})
    target_label = f"{target['exchange']} {target['symbol']}"
    source_label = f"{source_exchange} {source_symbol}"
    same_market = source_exchange == target["exchange"] and source_symbol == target["symbol"]

    if same_market:
        summary = (
            f"{target_label} {template.slug.replace('_', ' ')} as a same-market "
            f"OrderBook signal for {primary_horizon_seconds}s future mid return."
        )
    else:
        summary = (
            f"{source_label} {template.slug.replace('_', ' ')} as a leader-market "
            f"OrderBook signal for {target_label} {primary_horizon_seconds}s future mid return."
        )

    return {
        "schema_version": 1,
        "hypothesis": {
            "id": hypothesis_id,
            "parent_id": None,
            "title": (
                f"{source_label} {template.slug.replace('_', ' ')} predicts "
                f"{target_label} {primary_horizon_seconds}s mid return"
            ),
            "status": "draft",
            "family": template.family,
            "family_label": _FAMILY_LABELS[template.family],
            "custom_family_justification": None,
            "created_at": created_at,
            "owner": "agent",
            "summary": summary,
            "expected_edge_mechanism": template.mechanism,
            "economic_rationale": (
                f"{template.rationale} The {primary_horizon_seconds}s horizon is a draft default; "
                "before scanning, confirm the target market's volatility/spread wave shows this "
                "horizon can plausibly clear fees, spread, slippage, and latency."
            ),
            "duplicate_search": {
                "searched_at": created_at,
                "query_terms": _query_terms(template, source_exchange, source_symbol, target),
                "possible_duplicates": [],
                "conclusion": "new",
            },
            "data_requirements": {
                "markets": [
                    {"exchange": source_exchange, "symbol": source_symbol},
                    {"exchange": target["exchange"], "symbol": target["symbol"]},
                ],
                "datasets": [{"layer": "bronze", "dataset": "OrderBook"}],
                "minimum_distinct_days": 5,
                "required_columns": list(template.required_columns),
                "unavailable_data_policy": "needs-more-data",
                "research_metadata": None,
            },
            "feature": {
                "sql_expression": template.sql_expression,
                "source_datasets": [
                    {
                        "layer": "bronze",
                        "dataset": "OrderBook",
                        "exchange": source_exchange,
                        "symbol": source_symbol,
                        "alias": "ob",
                    }
                ],
                "primary_timestamp_column": "ts",
                "point_in_time_rule": ("feature computed strictly from OrderBook rows at or before signal ts; "
                                       "any window frames are finite and backward-looking"),
            },
            "cross_market_rationale": cross_market_rationale,
            "target": {
                "type": "next_return",
                "primary_horizon_seconds": primary_horizon_seconds,
                "comparison_horizons_seconds": list(comparison_horizons),
                "source_dataset": {
                    "layer": "bronze",
                    "dataset": "OrderBook",
                    "exchange": target["exchange"],
                    "symbol": target["symbol"],
                },
                "timestamp_cutoff_rule": "target window starts strictly after signal timestamp",
            },
            "execution_assumptions": {
                "order_type": _order_type_for_spec(target),
                "fees_bps": target["fee_bps"],
                "slippage_bps": default_slippage_bps(),
                "latency_ms": 100,
                "liquidity_capacity_assumption": "single-unit notional, no capacity scaling in v1 scan",
            },
            "falsification_tests": [
                {
                    "name": template.falsification_name,
                    "expected_failure_meaning": template.falsification_meaning,
                }
            ],
            "scan_plan": {
                "primary_horizon_seconds": primary_horizon_seconds,
                "threshold_grid": list(comparison_horizons),
                "regime_buckets": ["low_vol", "mid_vol", "high_vol"],
                "significance_method": "block_bootstrap_by_day",
            },
            "backtest_handoff_intent": {
                "potential_panda_core_strategy_pattern": (
                    "qate.trading.strategy variant, "
                    f"{template.family}-based entry"
                ),
                "himalaya_config_notes": (
                    f"{target['exchange']}/{target['symbol']}, "
                    "execution_size TBD from capacity scan"
                ),
            },
            "decisions": [
                {
                    "at": created_at,
                    "actor": "agent",
                    "decision": "draft",
                    "reason": "generated by tools/propose_expressions.py as an early-stage hypothesis seed",
                }
            ],
        },
    }


def propose_specs(
    specs_dir: Path = _DEFAULT_SPECS_DIR,
    date_str: str | None = None,
    created_at: str | None = None,
    limit: int | None = None,
    target_exchange: str | None = None,
    target_symbol: str | None = None,
    primary_horizon_seconds: int = 120,
) -> list[dict]:
    created_at = created_at or _utc_now_iso()
    date_str = date_str or created_at[:10].replace("-", "")
    next_seq = _next_sequence(specs_dir, date_str)
    specs: list[dict] = []

    for target in _target_markets(target_exchange, target_symbol):
        for template in _TEMPLATES:
            hypothesis_id = f"hyp-{date_str}-{next_seq:03d}"
            spec = build_spec(hypothesis_id, template, target, created_at, primary_horizon_seconds)
            errors = validate(spec)
            if errors:
                raise ValueError(f"generated invalid spec {hypothesis_id}: {errors}")
            specs.append(spec)
            next_seq += 1
            if limit is not None and len(specs) >= limit:
                return specs
    return specs


def write_specs(specs: Iterable[dict], specs_dir: Path) -> list[Path]:
    specs_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for spec in specs:
        hypothesis_id = spec["hypothesis"]["id"]
        path = specs_dir / f"{hypothesis_id}.yaml"
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing spec: {path}")
        path.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=False), encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate draft hypothesis specs from safe SQL expression templates")
    parser.add_argument("--specs-dir", default=str(_DEFAULT_SPECS_DIR))
    parser.add_argument("--date", help="hypothesis id date as YYYYMMDD; defaults to created_at date")
    parser.add_argument("--created-at", help="ISO timestamp; defaults to current UTC")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--target-exchange")
    parser.add_argument("--target-symbol")
    parser.add_argument("--primary-horizon-seconds", type=int, default=120)
    parser.add_argument("--write", action="store_true", help="write specs to specs-dir; otherwise print YAML documents")
    args = parser.parse_args()

    try:
        specs = propose_specs(
            specs_dir=Path(args.specs_dir),
            date_str=args.date,
            created_at=args.created_at,
            limit=args.limit,
            target_exchange=args.target_exchange,
            target_symbol=args.target_symbol,
            primary_horizon_seconds=args.primary_horizon_seconds,
        )
        if args.write:
            for path in write_specs(specs, Path(args.specs_dir)):
                print(path)
        else:
            print(yaml.safe_dump_all(specs, sort_keys=False, allow_unicode=False))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
