#!/usr/bin/env python3
"""Turn an approved handoff YAML into a Himalaya combo list to run.

Workflow stage 7: a human has approved a hypothesis (`backtest-approved` in the
ledger), the handoff YAML carries the frozen `himalaya_run_config` (the param
set(s) that survived selection). This tool is the mechanical bridge from that
YAML to a backtest you can run — no hand-copying param names into JSON.

A handoff only ever drives Himalaya's **combo** mode: it says *exactly which
param sets to run*, nothing more. So this tool touches exactly one file —
``params_combo.json`` in the target env — and lists the handoff's combo(s)
there in Himalaya vocabulary. Everything else about the env (``config.json``
market/order_size, ``trading.json`` profile, ``param_grid.json``, ``desc.json``)
is the env's own setup and is left untouched: the env must already exist and be
configured for backtesting.

It translates handoff param names to the env's combo vocabulary
(``sar_step`` -> ``step``, ``schedule`` -> ``schedule_version``, the ``gate.*``
block -> the ``rolling_dd_*`` combo fields), then provides ``run_backtest(...)``
(and a ``--run`` flag) that invokes Himalaya's ``start.sh --env <NAME>
--from-dt ... --to-dt ... --optimize combo`` — the entry point
``Himalaya/doc/backtest.md`` documents.

Nothing here flips ledger status or promotes anything: it consumes an
already-approved handoff and lists the run a human asked for.

Usage:
    python handoffs/build_backtest_env.py handoffs/hyp-20260713-002.yaml
    python handoffs/build_backtest_env.py handoffs/hyp-20260713-002.yaml --dry-run
    python handoffs/build_backtest_env.py handoffs/hyp-20260713-002.yaml --run
    python handoffs/build_backtest_env.py handoffs/hyp-20260713-002.yaml \
        --run --window forward
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
COMBO_FILE = "params_combo.json"

# Target market (EXCHANGE:SYMBOL) -> the configured env to run it in. A handoff
# does not set up an env; it only lists combos into an env that already exists.
KNOWN_ENVS: dict[str, str] = {
    "GMO:BTC_JPY": "QUINN_LEI",
}

# accel/max are not in the handoff combo (they were frozen platform-wide in the
# grid); default to the values the grid searched under. Override via CLI if a
# future handoff sweeps them.
DEFAULT_ACCELERATION = 0.02
DEFAULT_MAXIMUM = 0.2


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
    combos: list[Combo]
    windows: dict[str, tuple[str, str | None]]  # name -> (start, end)
    param_set_id: str | None
    raw: dict[str, Any]


def _as_market_str(entry: dict[str, Any]) -> str:
    return f"{entry['exchange']}:{entry['symbol']}"


def _combo_from_dict(
    combo_in: dict[str, Any], gate: dict[str, Any], where: str
) -> Combo:
    def _req_gate(key: str) -> Any:
        if key not in gate:
            raise ValueError(f"{where}: gate missing {key!r}")
        return gate[key]

    schedule = combo_in.get("schedule") or combo_in.get("schedule_version")
    if schedule is None:
        raise ValueError(f"{where}: combo missing schedule/schedule_version")
    step = combo_in.get("sar_step", combo_in.get("step"))
    if step is None:
        raise ValueError(f"{where}: combo missing sar_step/step")
    return Combo(
        schedule_version=schedule,
        box_size_rate=float(combo_in["box_size_rate"]),
        step=float(step),
        acceleration=float(combo_in.get("acceleration", DEFAULT_ACCELERATION)),
        maximum=float(combo_in.get("maximum", DEFAULT_MAXIMUM)),
        rolling_dd_window_seconds=int(_req_gate("rolling_dd_window_seconds")),
        rolling_dd_threshold_bps=int(_req_gate("rolling_dd_threshold_bps")),
        rolling_dd_cooldown_seconds=int(_req_gate("rolling_dd_cooldown_seconds")),
    )


def load_handoff(path: Path) -> Handoff:
    """Parse a handoff YAML into the combo list this tool writes.

    Accepts either a single ``combo`` mapping or a ``combos`` list under
    ``himalaya_run_config``. Raises ValueError with a precise message if a
    required field is missing, so a malformed handoff fails loudly here rather
    than deep inside Himalaya.
    """
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict) or "handoff" not in doc:
        raise ValueError(f"{path}: not a handoff YAML (missing top-level 'handoff')")
    h = doc["handoff"]
    rc = h.get("himalaya_run_config")
    if not rc:
        raise ValueError(f"{path}: handoff has no himalaya_run_config block")

    gate = rc.get("gate") or {}
    markets = rc.get("markets") or []

    combos_in = rc.get("combos")
    if combos_in is None:
        single = rc.get("combo")
        if not single:
            raise ValueError(f"{path}: himalaya_run_config has no combo/combos")
        combos_in = [single]

    combos = [
        _combo_from_dict(c, c.get("gate", gate), f"{path} combo[{i}]")
        for i, c in enumerate(combos_in)
    ]

    # Resolve the target market (EXCHANGE:SYMBOL) for env routing only.
    first = combos_in[0]
    target_symbol = first.get("target_symbol") or first.get("market")
    target_market = None
    if target_symbol:
        for m in markets:
            if m.get("symbol") == target_symbol:
                target_market = _as_market_str(m)
                break
    if target_market is None and markets:
        # Fall back to the non-leader market (the one that is not flip_symbol).
        flip = first.get("flip_symbol")
        for m in markets:
            if flip is None or m.get("symbol") != flip:
                target_market = _as_market_str(m)
    if target_market is None:
        raise ValueError(
            f"{path}: cannot resolve target market for env routing "
            f"(target_symbol={target_symbol!r}, markets={markets})"
        )

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
        combos=combos,
        windows=windows,
        param_set_id=rc.get("param_set_id"),
        raw=h,
    )


# ---------------------------------------------------------------------------
# Env name resolution
# ---------------------------------------------------------------------------


def resolve_env_name(handoff: Handoff, override: str | None = None) -> str:
    """Return the env name to write into. override wins; else KNOWN_ENVS."""
    if override:
        return override
    known = KNOWN_ENVS.get(handoff.target_market)
    if known:
        return known
    raise ValueError(
        f"No configured env for market {handoff.target_market!r}. "
        f"Add it to KNOWN_ENVS or pass --env <NAME>. The handoff only lists "
        f"combos; the env itself must already be set up under {ENV_ROOT}."
    )


# ---------------------------------------------------------------------------
# Writing the combo list
# ---------------------------------------------------------------------------


@dataclass
class WritePlan:
    env_name: str
    env_dir: Path
    combo_path: Path
    combos: list[dict[str, Any]]


def plan_env(handoff: Handoff, env_name: str) -> WritePlan:
    env_dir = ENV_ROOT / env_name
    return WritePlan(
        env_name=env_name,
        env_dir=env_dir,
        combo_path=env_dir / COMBO_FILE,
        combos=[c.to_dict() for c in handoff.combos],
    )


def apply_plan(plan: WritePlan) -> None:
    """Write params_combo.json only. The env must already exist."""
    if not plan.env_dir.is_dir():
        raise FileNotFoundError(
            f"Env {plan.env_name!r} not found at {plan.env_dir}. A handoff only "
            f"lists combos into an existing, configured env; set the env up first."
        )
    plan.combo_path.write_text(json.dumps(plan.combos, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Running the backtest
# ---------------------------------------------------------------------------


def _fmt_dt(d: str) -> str:
    return d.replace("-", "")


def run_backtest(
    env_name: str,
    from_dt: str,
    to_dt: str | None = None,
    start_sh: Path = HIMALAYA_START,
    extra_args: list[str] | None = None,
) -> int:
    """Invoke Himalaya start.sh in combo mode for this env. Returns exit code.

    from_dt/to_dt accept YYYY-MM-DD or YYYYMMDD. Always ``--optimize combo``:
    a handoff runs the explicit params_combo.json list, never a grid.
    """
    if not start_sh.exists():
        raise FileNotFoundError(f"Himalaya start.sh not found at {start_sh}")
    cmd = [str(start_sh), "--env", env_name, "--from-dt", _fmt_dt(from_dt)]
    if to_dt:
        cmd += ["--to-dt", _fmt_dt(to_dt)]
    cmd += ["--optimize", "combo"]
    if extra_args:
        cmd += extra_args
    print(f"[run] {' '.join(cmd)}", file=sys.stderr)
    return subprocess.call(cmd, cwd=start_sh.parent)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_summary(handoff: Handoff, plan: WritePlan, dry: bool) -> None:
    verb = "Would write" if dry else "Wrote"
    exists = plan.env_dir.is_dir()
    print(f"Handoff : {handoff.hypothesis_id}")
    print(f"Env     : {plan.env_name}   -> {plan.env_dir}"
          f"{'' if exists else '   [MISSING — set up the env first]'}")
    print(f"Target  : {handoff.target_market}")
    if handoff.param_set_id:
        print(f"Param id: {handoff.param_set_id}")
    print(f"{verb}  : {plan.combo_path.name}  ({len(plan.combos)} combo(s))")
    for c in plan.combos:
        print(f"          {json.dumps(c, sort_keys=True)}")
    if handoff.windows:
        print("Windows :")
        for name, (start, end) in handoff.windows.items():
            print(f"          {name:8s} {start} .. {end or '(open)'}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="List an approved handoff's combos into a Himalaya env's "
        "params_combo.json (and optionally run it)."
    )
    p.add_argument("handoff", type=Path, help="Path to the handoff YAML")
    p.add_argument("--env", help="Override the env name (default: KNOWN_ENVS map)")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be written; touch nothing")
    p.add_argument("--run", action="store_true",
                   help="Run the backtest (combo mode) after writing")
    p.add_argument("--window", default="holdout",
                   help="Which handoff window to run (train/holdout/forward), if --run")
    p.add_argument("--from-dt", help="Override run start (YYYY-MM-DD/YYYYMMDD)")
    p.add_argument("--to-dt", help="Override run end")
    args = p.parse_args(argv)

    handoff = load_handoff(args.handoff)
    env_name = resolve_env_name(handoff, args.env)
    plan = plan_env(handoff, env_name)

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
    return run_backtest(env_name, from_dt, to_dt)


if __name__ == "__main__":
    raise SystemExit(main())
