#!/usr/bin/env python3
"""Turn an approved handoff YAML into a runnable Himalaya backtest env.

Workflow stage 7: a human has approved a hypothesis (`backtest-approved` in the
ledger), the handoff YAML carries the frozen `himalaya_run_config` (the sole
surviving param set + windows + execution model). This tool is the mechanical
bridge from that YAML to a backtest you can actually run — no hand-copying
param names into JSON.

It does three things:

  1. Resolves the target env under ``~/env/backtest/<NAME>/``. Known markets map
     to an existing env (BTC_JPY -> QUINN_LEI); an unknown market gets a fresh
     env folder created from the QUINN_LEI template.
  2. Writes the env's JSON config, translating handoff param names to the env's
     param names (``sar_step`` -> ``step``, ``schedule`` -> ``schedule_version``,
     the ``gate.*`` block -> the ``rolling_dd_*`` combo fields, ``markets`` +
     ``execution_size`` -> ``config.json``). For an env that already exists we
     only rewrite the config + param files and leave ``trading.json`` /
     ``features.json`` / ``desc.json`` untouched.
  3. Provides ``run_backtest(...)`` (and a ``--run`` flag) that invokes
     Himalaya's ``start.sh --env <NAME> --from-dt ... --to-dt ... --optimize
     combo`` — the same entry point ``doc/backtest.md`` documents.

Nothing here flips ledger status or promotes anything: it consumes an
already-approved handoff and prepares the run a human asked for.

Usage:
    python handoffs/build_backtest_env.py handoffs/hyp-20260713-002.yaml
    python handoffs/build_backtest_env.py handoffs/hyp-20260713-002.yaml --dry-run
    python handoffs/build_backtest_env.py handoffs/hyp-20260713-002.yaml --run
    python handoffs/build_backtest_env.py handoffs/hyp-20260713-002.yaml \
        --run --window forward --optimize combo
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

ENV_ROOT = Path.home() / "env" / "backtest"
HIMALAYA_START = Path.home() / "workspace" / "Himalaya" / "start.sh"

# Target market (EXCHANGE:SYMBOL) -> already-configured env name. When a handoff
# resolves to a market listed here, we only rewrite that env's config + params;
# we never clobber its trading profile. Anything not listed gets a new env.
KNOWN_ENVS: dict[str, str] = {
    "GMO:BTC_JPY": "QUINN_LEI",
}

# accel/max are not in the handoff combo (they were frozen platform-wide in the
# grid); default to the values the grid searched under. Override via CLI if a
# future handoff sweeps them.
DEFAULT_ACCELERATION = 0.02
DEFAULT_MAXIMUM = 0.2
DEFAULT_MAGNIFIER = 5.0

# Defaults for a freshly created env — mirror the QUINN_LEI template so a new
# env is immediately loadable by qate.env.Env.
_DESC_MODULES = {
    "config": "orion.config.Config",
    "features": "builtins.dict",
    "param_grid": "builtins.dict",
    "params": "builtins.dict",
    "trading": "qate.boot.config.TradingProfile",
}
_DEFAULT_TRADING = {
    "chat": None,
    "gateway": "SIMULATOR",
    "strategy_module_name": "orion",
    "trading_api": "DEFAULT",
    "variant": "v601",
}
_DEFAULT_FEATURES = {"INVENTORY_HEALTH_CHECK": False}


# ---------------------------------------------------------------------------
# Handoff parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Combo:
    """One frozen parameter set, in env (params_combo.json) vocabulary."""

    schedule_version: str
    box_size_rate: float
    step: float
    acceleration: float
    maximum: float
    rolling_dd_window_seconds: int
    rolling_dd_threshold_bps: int
    rolling_dd_cooldown_seconds: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule_version": self.schedule_version,
            "box_size_rate": self.box_size_rate,
            "step": self.step,
            "acceleration": self.acceleration,
            "maximum": self.maximum,
            "rolling_dd_window_seconds": self.rolling_dd_window_seconds,
            "rolling_dd_threshold_bps": self.rolling_dd_threshold_bps,
            "rolling_dd_cooldown_seconds": self.rolling_dd_cooldown_seconds,
        }


@dataclass(frozen=True)
class Handoff:
    hypothesis_id: str
    target_market: str  # "GMO:BTC_JPY"
    ref_market: str  # "BINANCE:BTC_USDT"
    order_size: float
    combo: Combo
    windows: dict[str, tuple[str, str | None]]  # name -> (start, end)
    param_set_id: str | None
    raw: dict[str, Any]


def _as_market_str(entry: dict[str, Any]) -> str:
    return f"{entry['exchange']}:{entry['symbol']}"


def load_handoff(path: Path) -> Handoff:
    """Parse a handoff YAML into the fields this tool needs.

    Raises ValueError with a precise message if a required field is missing, so
    a malformed handoff fails loudly here rather than deep inside Himalaya.
    """
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict) or "handoff" not in doc:
        raise ValueError(f"{path}: not a handoff YAML (missing top-level 'handoff')")
    h = doc["handoff"]
    rc = h.get("himalaya_run_config")
    if not rc:
        raise ValueError(f"{path}: handoff has no himalaya_run_config block")

    combo_in = rc.get("combo") or {}
    gate = rc.get("gate") or {}
    markets = rc.get("markets") or []

    target_symbol = combo_in.get("target_symbol") or combo_in.get("market")
    flip_symbol = combo_in.get("flip_symbol")
    if not target_symbol:
        raise ValueError(f"{path}: combo missing target_symbol/market")

    # Resolve full EXCHANGE:SYMBOL from the markets list. The target is the
    # market whose symbol matches target_symbol; the ref/leader is the other.
    target_market = ref_market = None
    for m in markets:
        if m.get("symbol") == target_symbol:
            target_market = _as_market_str(m)
        elif flip_symbol is None or m.get("symbol") == flip_symbol:
            ref_market = _as_market_str(m)
    if target_market is None:
        raise ValueError(
            f"{path}: target_symbol {target_symbol!r} not found in markets {markets}"
        )
    if ref_market is None:
        raise ValueError(f"{path}: could not resolve ref/leader market from {markets}")

    exec_size = combo_in.get("execution_size")
    if exec_size is None:
        sizes = (rc.get("execution_model") or {}).get("execution_sizes") or {}
        exec_size = sizes.get(target_symbol)
    if exec_size is None:
        raise ValueError(f"{path}: no execution_size for {target_symbol}")

    def _req(d: dict, key: str) -> Any:
        if key not in d:
            raise ValueError(f"{path}: gate missing {key!r}")
        return d[key]

    combo = Combo(
        schedule_version=combo_in.get("schedule") or combo_in.get("schedule_version"),
        box_size_rate=float(combo_in["box_size_rate"]),
        step=float(combo_in.get("sar_step", combo_in.get("step"))),
        acceleration=float(combo_in.get("acceleration", DEFAULT_ACCELERATION)),
        maximum=float(combo_in.get("maximum", DEFAULT_MAXIMUM)),
        rolling_dd_window_seconds=int(_req(gate, "rolling_dd_window_seconds")),
        rolling_dd_threshold_bps=int(_req(gate, "rolling_dd_threshold_bps")),
        rolling_dd_cooldown_seconds=int(_req(gate, "rolling_dd_cooldown_seconds")),
    )
    if combo.schedule_version is None:
        raise ValueError(f"{path}: combo missing schedule/schedule_version")

    windows: dict[str, tuple[str, str | None]] = {}
    for name in ("train_window", "holdout_window", "forward_window"):
        w = rc.get(name)
        if not w:
            continue
        short = name.replace("_window", "")
        end = w.get("end_date")
        # forward windows are often open-ended with a prose end ("open — ...")
        if end and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(end)):
            end = None
        windows[short] = (w["start_date"], end)

    return Handoff(
        hypothesis_id=h.get("hypothesis_id", path.stem),
        target_market=target_market,
        ref_market=ref_market,
        order_size=float(exec_size),
        combo=combo,
        windows=windows,
        param_set_id=rc.get("param_set_id"),
        raw=h,
    )


# ---------------------------------------------------------------------------
# Env name resolution
# ---------------------------------------------------------------------------


def _derive_env_name(target_market: str) -> str:
    """Fabricate a valid EnvName ([A-Z_]+) from EXCHANGE:SYMBOL.

    EnvName forbids digits, so we strip them. e.g. GMO:ETH_JPY -> GMO_ETH_JPY.
    """
    raw = target_market.replace(":", "_").upper()
    name = re.sub(r"[^A-Z_]", "", raw).strip("_")
    name = re.sub(r"_+", "_", name)
    if not name:
        raise ValueError(f"Cannot derive an EnvName from market {target_market!r}")
    return name


def resolve_env_name(handoff: Handoff, override: str | None = None) -> tuple[str, bool]:
    """Return (env_name, is_known). override wins; then KNOWN_ENVS; else derive."""
    if override:
        return override, (ENV_ROOT / override).exists()
    known = KNOWN_ENVS.get(handoff.target_market)
    if known:
        return known, True
    return _derive_env_name(handoff.target_market), False


# ---------------------------------------------------------------------------
# Env file construction
# ---------------------------------------------------------------------------


def build_config(handoff: Handoff, existing: dict | None) -> dict[str, Any]:
    """config.json — market/ref_market/order_size from the handoff.

    Preserve an existing magnifier (a venue/account property, not something the
    handoff owns); default it only for a brand-new env.
    """
    magnifier = (existing or {}).get("magnifier", DEFAULT_MAGNIFIER)
    return {
        "magnifier": magnifier,
        "market": handoff.target_market,
        "order_size": handoff.order_size,
        "ref_market": handoff.ref_market,
    }


def build_param_grid(combo: Combo) -> list[dict[str, Any]]:
    """A single-point grid: every knob pinned to the frozen combo value.

    The handoff is a *frozen* param set — no parameter freedom left — so the
    grid degenerates to one point. This keeps ``--optimize grid`` valid and
    documents the frozen values in the env, but combo mode is the intended run.
    """
    return [
        {"name": "schedule_version", "default_value": combo.schedule_version,
         "values": [combo.schedule_version]},
        {"name": "box_size_rate", "default_value": combo.box_size_rate,
         "factor": 100000, "values": [combo.box_size_rate]},
        {"name": "step", "default_value": combo.step,
         "factor": 1000, "values": [combo.step]},
        {"name": "acceleration", "default_value": combo.acceleration,
         "factor": 1000, "values": [combo.acceleration]},
        {"name": "maximum", "default_value": combo.maximum,
         "factor": 1000, "values": [combo.maximum]},
        {"name": "rolling_dd_window_seconds",
         "default_value": combo.rolling_dd_window_seconds, "factor": 1,
         "values": [combo.rolling_dd_window_seconds]},
        {"name": "rolling_dd_threshold_bps",
         "default_value": combo.rolling_dd_threshold_bps, "factor": 1,
         "values": [combo.rolling_dd_threshold_bps]},
    ]


def build_params(combo: Combo) -> dict[str, Any]:
    """params.json — the default single param set (combo vocabulary)."""
    return combo.to_dict()


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


@dataclass
class WritePlan:
    env_name: str
    env_dir: Path
    is_known: bool
    files: dict[str, Any]  # filename -> content to write
    preserved: list[str]  # files left untouched on an existing env


def plan_env(handoff: Handoff, env_name: str, is_known: bool) -> WritePlan:
    """Compute exactly which files we would write, without touching disk."""
    env_dir = ENV_ROOT / env_name
    exists = env_dir.exists()

    existing_config = None
    cfg_path = env_dir / "config.json"
    if cfg_path.exists():
        existing_config = json.loads(cfg_path.read_text())

    files: dict[str, Any] = {
        "config.json": build_config(handoff, existing_config),
        "params_combo.json": [handoff.combo.to_dict()],
        "params.json": build_params(handoff.combo),
        "param_grid.json": build_param_grid(handoff.combo),
    }
    preserved: list[str] = []

    # Scaffolding files: write only when missing, so we never clobber a
    # human-tuned trading profile on an existing env.
    scaffold = {
        "desc.json": {"modules": _DESC_MODULES, "name": env_name},
        "trading.json": _DEFAULT_TRADING,
        "features.json": _DEFAULT_FEATURES,
    }
    for fname, default in scaffold.items():
        if (env_dir / fname).exists():
            preserved.append(fname)
        else:
            files[fname] = default

    return WritePlan(
        env_name=env_name,
        env_dir=env_dir,
        is_known=is_known and exists,
        files=files,
        preserved=preserved,
    )


def apply_plan(plan: WritePlan) -> None:
    plan.env_dir.mkdir(parents=True, exist_ok=True)
    for fname, content in plan.files.items():
        _write_json(plan.env_dir / fname, content)


# ---------------------------------------------------------------------------
# Running the backtest
# ---------------------------------------------------------------------------


def _fmt_dt(d: str) -> str:
    return d.replace("-", "")


def run_backtest(
    env_name: str,
    from_dt: str,
    to_dt: str | None = None,
    optimize: str | None = "combo",
    start_sh: Path = HIMALAYA_START,
    extra_args: list[str] | None = None,
) -> int:
    """Invoke Himalaya's start.sh for this env. Returns the process exit code.

    from_dt/to_dt accept YYYY-MM-DD or YYYYMMDD. optimize: "combo" (default,
    runs params_combo.json), "grid", or None (single default run).
    """
    if not start_sh.exists():
        raise FileNotFoundError(f"Himalaya start.sh not found at {start_sh}")
    cmd = [str(start_sh), "--env", env_name, "--from-dt", _fmt_dt(from_dt)]
    if to_dt:
        cmd += ["--to-dt", _fmt_dt(to_dt)]
    if optimize:
        cmd += ["--optimize", optimize]
    if extra_args:
        cmd += extra_args
    print(f"[run] {' '.join(cmd)}", file=sys.stderr)
    return subprocess.call(cmd, cwd=start_sh.parent)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_summary(handoff: Handoff, plan: WritePlan, dry: bool) -> None:
    verb = "Would write" if dry else "Wrote"
    kind = "existing env (configs only)" if plan.is_known else "new env"
    print(f"Handoff : {handoff.hypothesis_id}")
    print(f"Env     : {plan.env_name}  [{kind}]  -> {plan.env_dir}")
    print(f"Target  : {handoff.target_market}   ref {handoff.ref_market}   "
          f"size {handoff.order_size}")
    if handoff.param_set_id:
        print(f"Param id: {handoff.param_set_id}")
    print(f"Combo   : {json.dumps(handoff.combo.to_dict())}")
    if plan.preserved:
        print(f"Kept    : {', '.join(sorted(plan.preserved))}")
    print(f"{verb}  : {', '.join(sorted(plan.files))}")
    if handoff.windows:
        print("Windows :")
        for name, (start, end) in handoff.windows.items():
            print(f"          {name:8s} {start} .. {end or '(open)'}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Build (and optionally run) a Himalaya backtest env from a handoff YAML."
    )
    p.add_argument("handoff", type=Path, help="Path to the handoff YAML")
    p.add_argument("--env", help="Override the env name (default: known map / derived)")
    p.add_argument("--dry-run", action="store_true", help="Show what would be written; touch nothing")
    p.add_argument("--run", action="store_true", help="Run the backtest after writing the env")
    p.add_argument("--window", default="holdout",
                   help="Which handoff window to run (train/holdout/forward), if --run")
    p.add_argument("--from-dt", help="Override run start (YYYY-MM-DD/YYYYMMDD)")
    p.add_argument("--to-dt", help="Override run end")
    p.add_argument("--optimize", choices=["grid", "combo"], default="combo",
                   help="Himalaya optimize mode for --run (default: combo)")
    args = p.parse_args(argv)

    handoff = load_handoff(args.handoff)
    env_name, is_known = resolve_env_name(handoff, args.env)
    plan = plan_env(handoff, env_name, is_known)

    if not args.dry_run:
        apply_plan(plan)
    _print_summary(handoff, plan, dry=args.dry_run)

    if not args.run:
        return 0
    if args.dry_run:
        print("\n--run ignored under --dry-run.", file=sys.stderr)
        return 0

    from_dt = args.from_dt
    to_dt = args.to_dt
    if from_dt is None:
        if args.window not in handoff.windows:
            print(f"\nNo '{args.window}' window in handoff and no --from-dt given.",
                  file=sys.stderr)
            return 2
        w_start, w_end = handoff.windows[args.window]
        from_dt = w_start
        to_dt = to_dt if to_dt is not None else w_end
    print()
    return run_backtest(env_name, from_dt, to_dt, optimize=args.optimize)


if __name__ == "__main__":
    raise SystemExit(main())
