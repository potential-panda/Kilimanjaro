"""Versioned campaign contract and explicit, timezone-aware data roles."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .identity import identity, normalize


def instant(value: str) -> float:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("Timestamps require an explicit timezone")
    return dt.timestamp()


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_.-]{0,100}", value
    ):
        raise ValueError(f"Invalid identifier: {value!r}")
    return value


def positive(value, name: str, *, zero: bool = False) -> float:
    import math

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be a finite number")
    if value < 0 or (value == 0 and not zero):
        raise ValueError(f"{name} must be {'nonnegative' if zero else 'positive'}")
    return value


@dataclass(frozen=True)
class Campaign:
    spec: dict

    @classmethod
    def load(cls, path: Path) -> Campaign:
        with Path(path).open() as stream:
            return cls.from_dict(yaml.safe_load(stream))

    @classmethod
    def from_dict(cls, spec: dict) -> Campaign:
        from kilimanjaro.signal.expression import validate_expr

        spec = normalize(copy.deepcopy(spec))
        import json

        from jsonschema import Draft202012Validator

        schema = (
            Path(__file__).resolve().parents[3]
            / "cycle"
            / "01_schemas"
            / "research_campaign.schema.json"
        )
        errors = list(
            Draft202012Validator(json.loads(schema.read_text())).iter_errors(spec)
        )
        if errors:
            raise ValueError(
                "Invalid campaign: " + "; ".join(error.message for error in errors[:5])
            )
        required = {
            "schema_version",
            "name",
            "program",
            "data_root",
            "research_root",
            "markets",
            "target",
            "features",
            "windows",
            "horizons",
            "execution",
            "budgets",
            "seed",
            "warmup_seconds",
            "max_staleness_seconds",
            "trial_policy",
        }
        optional = {"description", "hypothesis", "falsification", "provider_params"}
        if not isinstance(spec, dict) or required - spec.keys():
            raise ValueError(
                f"Missing campaign fields: {sorted(required - (spec.keys() if isinstance(spec, dict) else set()))}"
            )
        if spec.keys() - required - optional:
            raise ValueError(
                f"Unknown campaign fields: {sorted(spec.keys() - required - optional)}"
            )
        if spec["schema_version"] != 1:
            raise ValueError("Unsupported campaign schema")
        identifier(spec["name"])
        identifier(spec["program"])
        for key in ("data_root", "research_root"):
            if not isinstance(spec[key], str) or not Path(spec[key]).is_absolute():
                raise ValueError(f"{key} must be an absolute path")
        if not spec["markets"] or spec["target"] not in spec["markets"]:
            raise ValueError("Target must name an explicit market alias")
        for alias, market in spec["markets"].items():
            if not re.fullmatch(r"[a-z][a-z0-9]*", alias):
                raise ValueError("Market aliases must be lowercase alphanumeric")
            if set(market) != {"exchange", "symbol", "execution_size"}:
                raise ValueError("Market requires exchange, symbol, execution_size")
            for key in ("exchange", "symbol"):
                identifier(market[key])
            positive(float(market["execution_size"]), "execution_size")
            # Match the existing provider's partition spelling, e.g. 150.0 vs 150.
            market["execution_size"] = str(float(market["execution_size"]))
        if set(spec["windows"]) != {"train", "validation"}:
            raise ValueError("Development campaign requires train and validation only")
        for window in spec["windows"].values():
            if set(window) != {"start", "end"} or instant(window["start"]) >= instant(
                window["end"]
            ):
                raise ValueError("Windows are nonempty [start,end) intervals")
        if instant(spec["windows"]["train"]["end"]) > instant(
            spec["windows"]["validation"]["start"]
        ):
            raise ValueError("Training must precede validation")
        if not spec["horizons"] or len(set(spec["horizons"])) != len(spec["horizons"]):
            raise ValueError("Horizons must be nonempty and unique")
        for h in spec["horizons"]:
            if type(h) is not int or h <= 0:
                raise ValueError("Horizons must be positive integer seconds")
        if type(spec["seed"]) is not int or spec["seed"] < 0:
            raise ValueError("Seed must be a nonnegative integer")
        for key in ("warmup_seconds", "max_staleness_seconds"):
            positive(spec[key], key, zero=True)
            if type(spec[key]) is not int:
                raise ValueError(f"{key} must be integer seconds")
        names = set()
        if not spec["features"]:
            raise ValueError("At least one feature is required")
        if len(spec["features"]) > 32:
            raise ValueError("v1 caps a campaign at 32 features; batch larger catalogs")
        for feature in spec["features"]:
            if set(feature) != {"name", "expr"}:
                raise ValueError("Features require name and expr")
            identifier(feature["name"])
            if feature["name"] in names:
                raise ValueError("Feature names must be unique")
            names.add(feature["name"])
            for column in validate_expr(feature["expr"]):
                if column.split("_", 1)[0] not in spec["markets"]:
                    raise ValueError(f"Unknown market alias in {column}")
        execution = spec["execution"]
        if set(execution) != {
            "threshold",
            "round_trip_fee_bps",
            "round_trip_slippage_bps",
            "max_quote_delay_seconds",
        }:
            raise ValueError(
                "Execution requires threshold, round_trip_fee_bps, round_trip_slippage_bps, max_quote_delay_seconds"
            )
        for key, value in execution.items():
            positive(value, key, zero=key != "threshold")
        budgets = spec["budgets"]
        if set(budgets) != {
            "max_jobs",
            "daily_jobs",
            "memory_mb",
            "disk_mb",
            "job_timeout_seconds",
            "max_attempts",
            "max_variants",
        }:
            raise ValueError("Incomplete or unknown resource budget")
        for key, value in budgets.items():
            positive(value, key)
            if type(value) is not int:
                raise ValueError(f"{key} must be an integer")
        if (2 * len(spec["features"]) + 4) * len(spec["horizons"]) > budgets[
            "max_variants"
        ]:
            raise ValueError("Feature/horizon search exceeds max_variants")
        policy = spec["trial_policy"]
        policy.setdefault("min_coverage", 0.95)
        if set(policy) != {
            "program_alpha",
            "min_trades",
            "min_days",
            "bootstrap_samples",
            "max_drawdown_bps",
            "max_trades_per_day",
            "max_profit_day_share",
            "min_coverage",
        }:
            raise ValueError("Incomplete or unknown confirmation policy")
        for key, value in policy.items():
            positive(value, key)
        for key in (
            "min_trades",
            "min_days",
            "bootstrap_samples",
            "max_trades_per_day",
        ):
            if type(policy[key]) is not int:
                raise ValueError(f"{key} must be an integer")
        if spec.get("provider_params"):
            raise ValueError(
                "Provider overrides require a registered loader/partition contract; v1 uses provider defaults"
            )
        if (
            not 0 < policy["program_alpha"] < 1
            or not 0 < policy["max_profit_day_share"] <= 1
        ):
            raise ValueError("Invalid probability in confirmation policy")
        if not 0 < policy["min_coverage"] <= 1:
            raise ValueError("min_coverage must be in (0,1]")
        if (
            policy["min_trades"] < 20
            or policy["min_days"] < 2
            or policy["bootstrap_samples"] < 100
        ):
            raise ValueError(
                "Confirmation requires >=20 trades, >=2 days and >=100 resamples"
            )
        return cls(spec)

    @property
    def logical_id(self) -> str:
        return identity(
            "campaign",
            {
                k: v
                for k, v in self.spec.items()
                if k not in {"data_root", "research_root"}
            },
        )

    @property
    def root(self) -> Path:
        return Path(self.spec["research_root"])

    @property
    def data_root(self) -> Path:
        return Path(self.spec["data_root"])

    @property
    def database(self) -> Path:
        return self.root / "17_ledger" / "research.sqlite"
