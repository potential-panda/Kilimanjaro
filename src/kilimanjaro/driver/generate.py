"""The formula generator that keeps the loop's queue full.

Enumerates economic formula *families* over the available markets, gated by what
data actually exists, deduplicated against every scan spec already proposed, and
emits new scan specs (with fresh hypothesis ids) into the queue. The scanner loop
driver then scans them. Together they never run dry.

Design choices:
- **Templates, not random search.** Each family encodes a hypothesis (momentum,
  order-flow imbalance, book imbalance, mean-reversion, cross-market basis).
  Every signal is z-scored so one threshold scale (in std units) fits all.
- **Data-gated.** A (family, market-pair) is only proposed if the signal source
  actually has the feature and the target has MarketPrice; execution sizes are
  read from the data, never guessed.
- **Deduped by formula signature** (markets + target + return_calc + expressions,
  independent of the threshold/horizon sweep), so the same idea is never queued
  twice — against hand-written and previously-generated specs alike.
- **Diversified.** Proposals round-robin across families so a bounded batch gets
  variety, not 20 momentum variants.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

from kilimanjaro.data.feature_frame import available_features

_DEFAULT_DATA_ROOT = Path("/panda/makalu-data")

# (leader exchange, leader symbol, target exchange, target symbol).
# Leaders are the BINANCE lead-lag source; targets are execution venues.
PAIRS = [
    ("BINANCE", "ETH_USDT", "GMO", "ETH_JPY"),
    ("BINANCE", "BTC_USDT", "GMO", "BTC_JPY"),
    ("BINANCE", "XRP_USDT", "GMO", "XRP_JPY"),
    ("BINANCE", "ETH_USDT", "BITBANK", "ETH_SPOT"),
]

# Shared sweep: the driver permutes these per generated formula.
DEFAULT_SWEEP = {
    "thresholds": [1.5, 2.5],
    "age_windows": [[0, 60], [0, 300]],
    "horizons": [30, 120, 300],
    "invert": [False, True],
    "return_calc": "fixed_horizon",
}


def _momentum(a):
    return [(f"mom_{w}s_z", f"zscore(ret({a}_mid, {w}), 1800)") for w in (30, 120, 300)]


def _imbalance(a):
    out = [(f"imb_{w}s_z", f"zscore({a}_ta_imbalance_qty_{w}s, 1800)") for w in (5, 30)]
    out += [(f"netflow_{w}s_z", f"zscore({a}_ta_net_flow_{w}s, 1800)") for w in (10,)]
    return out


def _obi(a):
    return [
        (f"obi_k{d}_{hl}s_z", f"zscore({a}_obi_ema_k{d}_{hl}s, 1800)")
        for d in (1, 5)
        for hl in (30, 120)
    ]


def _reversion(a):
    return [
        (f"revert_{w}s_z", f"zscore({a}_mid / ema({a}_mid, {w}) - 1, 1800)")
        for w in (300, 900)
    ]


def _basis(a, b):
    return [(f"basis_{w}_z", f"zscore({a}_mid / {b}_mid, {w})") for w in (900, 3600)]


# family: needs (feature group on the leader), cross (uses target mid too), build
FAMILIES = [
    {"family": "momentum", "needs": "mid", "cross": False, "build": _momentum},
    {"family": "orderflow", "needs": "ta", "cross": False, "build": _imbalance},
    {"family": "obi", "needs": "obi", "cross": False, "build": _obi},
    {"family": "reversion", "needs": "mid", "cross": False, "build": _reversion},
    {"family": "basis", "needs": "mid", "cross": True, "build": _basis},
]


def _market_features(exchange: str, symbol: str, data_root: Path) -> dict:
    av = available_features(exchange, symbol, data_root)
    sizes = [
        k.split("execution_size=")[1].rstrip(")")
        for k in av
        if k.startswith("mid") and "execution_size=" in k
    ]
    return {
        "has_mid": bool(sizes),
        "sizes": sizes,
        "has_obi": any("OBIFeature" in k for k in av),
        "has_ta": any("TradeAggression" in k for k in av),
    }


def _pick_size(sizes: list[str], largest: bool) -> str:
    return sorted(sizes, key=lambda s: float(s), reverse=largest)[0]


def _viable(fam: dict, lf: dict, tf: dict) -> bool:
    if not tf["has_mid"]:  # need target MarketPrice for returns
        return False
    if fam["cross"]:
        return lf["has_mid"]
    return {"mid": lf["has_mid"], "ta": lf["has_ta"], "obi": lf["has_obi"]}[
        fam["needs"]
    ]


def _spec_for(fam: dict, pair: tuple, lf: dict, tf: dict) -> dict:
    lex, lsym, tex, tsym = pair
    lead_size = _pick_size(lf["sizes"], largest=False)
    targ_size = _pick_size(tf["sizes"], largest=True)
    markets = {"a": {"exchange": lex, "symbol": lsym, "execution_size": lead_size}}
    if fam["cross"]:
        markets["b"] = {"exchange": tex, "symbol": tsym, "execution_size": targ_size}
        exprs_raw = fam["build"]("a", "b")
    else:
        exprs_raw = fam["build"]("a")
    return {
        "family": fam["family"],
        "mechanism": f"{fam['family']} signal from {lex}/{lsym} predicting {tex}/{tsym}",
        "generated": True,
        "markets": markets,
        "target": {"exchange": tex, "symbol": tsym, "execution_size": targ_size},
        "return_calc": DEFAULT_SWEEP["return_calc"],
        "expressions": [
            {"label": lbl, "expr": expr, "invert": DEFAULT_SWEEP["invert"]}
            for lbl, expr in exprs_raw
        ],
        "thresholds": DEFAULT_SWEEP["thresholds"],
        "age_windows": DEFAULT_SWEEP["age_windows"],
        "horizons": DEFAULT_SWEEP["horizons"],
    }


def signature(spec: dict) -> str:
    """Window/sweep-independent id for a formula idea, for dedup."""
    markets = sorted(
        (m["exchange"], m["symbol"]) for m in spec.get("markets", {}).values()
    )
    t = spec.get("target", {})
    exprs = sorted(e["expr"] for e in spec.get("expressions", []))
    return json.dumps(
        {
            "rc": spec.get("return_calc", "fixed_horizon"),
            "m": markets,
            "t": [t.get("exchange"), t.get("symbol")],
            "e": exprs,
        },
        sort_keys=True,
    )


def existing_signatures(*dirs: Path) -> set[str]:
    """Signatures of every scan-spec already on disk (so we never re-propose)."""
    sigs: set[str] = set()
    for d in dirs:
        for path in Path(d).rglob("*.yaml"):
            try:
                spec = yaml.safe_load(path.read_text())
            except Exception:  # noqa: BLE001 — a bad file shouldn't block generation
                continue
            if isinstance(spec, dict) and spec.get("expressions"):
                sigs.add(signature(spec))
    return sigs


def next_seq(date_str: str, *scan_dirs: Path) -> int:
    """Next hyp-<date>-NNN sequence, continuing past ids already used in specs."""
    pat = re.compile(rf"hyp-{date_str}-(\d+)")
    used = [0]
    for d in scan_dirs:
        for path in Path(d).rglob("*.yaml"):
            for m in pat.finditer(path.read_text()):
                used.append(int(m.group(1)))
    return max(used) + 1


def _priority_order(
    families: list[dict], family_priority: dict[str, float] | None
) -> list[int]:
    """Family indices ordered by feedback priority (desc), stable for ties.

    None -> identity (equal round-robin). Families that have produced survivors
    lead, so within a bounded batch they contribute more proposals.
    """
    order = list(range(len(families)))
    if family_priority:
        order.sort(key=lambda i: -family_priority.get(families[i]["family"], 0.0))
    return order


def family_scores(
    *scan_dirs: Path, ledger_path: Path | None = None
) -> dict[str, float]:
    """Per-family count of hypotheses that reached scan-passed or better.

    Joins the ledger (outcomes, by hypothesis_id) to the specs on disk (which
    carry `family`). This is the generator's feedback signal — bias toward
    families that have actually survived. Empty when there is no history yet,
    so generation falls back to equal round-robin.
    """
    from kilimanjaro.ledger.core import load_events

    hyp_family: dict[str, str] = {}
    for d in scan_dirs:
        for path in Path(d).rglob("*.yaml"):
            try:
                spec = yaml.safe_load(path.read_text())
            except Exception:  # noqa: BLE001
                continue
            if (
                isinstance(spec, dict)
                and spec.get("family")
                and spec.get("hypothesis_id")
            ):
                hyp_family[spec["hypothesis_id"]] = spec["family"]

    good = {"scan-passed", "backtest-approved", "promoted", "live"}
    scores: dict[str, float] = {}
    events = load_events(ledger_path) if ledger_path else load_events()
    for ev in events:
        if ev.get("to_status") in good:
            fam = hyp_family.get(ev.get("hypothesis_id"))
            if fam:
                scores[fam] = scores.get(fam, 0.0) + 1.0
    return scores


def propose_specs(
    limit: int,
    date_str: str,
    *,
    seen: set[str] | None = None,
    seq_start: int = 1,
    data_root: Path = _DEFAULT_DATA_ROOT,
    pairs: list[tuple] = PAIRS,
    families: list[dict] = FAMILIES,
    family_priority: dict[str, float] | None = None,
) -> list[dict]:
    """Return up to `limit` new, deduped, data-viable scan specs with hyp ids.

    `family_priority` (e.g. from family_scores) biases which families lead —
    feedback toward what has survived. None keeps the equal round-robin.
    """
    feat: dict[tuple, dict] = {}

    def features(ex, sym):
        key = (ex, sym)
        if key not in feat:
            feat[key] = _market_features(ex, sym, data_root)
        return feat[key]

    # per-family candidate lists, for round-robin diversity
    buckets: list[list[dict]] = []
    for fam in families:
        col = []
        for pair in pairs:
            lex, lsym, tex, tsym = pair
            lf, tf = features(lex, lsym), features(tex, tsym)
            if _viable(fam, lf, tf):
                col.append(_spec_for(fam, pair, lf, tf))
        buckets.append(col)

    # Feedback: order families by past success, then round-robin for diversity.
    buckets = [buckets[i] for i in _priority_order(families, family_priority)]
    ordered: list[dict] = []
    for i in range(max((len(b) for b in buckets), default=0)):
        for b in buckets:
            if i < len(b):
                ordered.append(b[i])

    out: list[dict] = []
    seen = set(seen or ())
    seq = seq_start
    for spec in ordered:
        sig = signature(spec)
        if sig in seen:
            continue
        seen.add(sig)
        spec = {"hypothesis_id": f"hyp-{date_str}-{seq:03d}", **spec}
        seq += 1
        out.append(spec)
        if len(out) >= limit:
            break
    return out


def write_specs(specs: list[dict], queue_dir: Path) -> list[Path]:
    """Write each spec to queue_dir as <hyp_id>_<family>_<target>.yaml."""
    queue_dir = Path(queue_dir)
    queue_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for spec in specs:
        t = spec["target"]
        name = (
            f"{spec['hypothesis_id']}_{spec.get('family', 'expr')}_{t['symbol']}.yaml"
        )
        path = queue_dir / name
        path.write_text(yaml.safe_dump(spec, sort_keys=False))
        paths.append(path)
    return paths


def generate(
    queue_dir: Path,
    limit: int,
    date_str: str,
    *,
    scan_dirs: list[Path] | None = None,
    data_root: Path = _DEFAULT_DATA_ROOT,
    feedback: bool = True,
) -> list[Path]:
    """Full stage-B step: dedup against existing specs, propose, write to queue.

    feedback=True biases family selection toward what has survived (family_scores
    from the ledger); it degrades to equal round-robin when there is no history.
    """
    scan_dirs = scan_dirs or [Path("cycle/12_specs")]
    seen = existing_signatures(*scan_dirs)
    seq = next_seq(date_str, *scan_dirs)
    prio = family_scores(*scan_dirs) if feedback else None
    specs = propose_specs(
        limit,
        date_str,
        seen=seen,
        seq_start=seq,
        data_root=data_root,
        family_priority=prio,
    )
    paths = write_specs(specs, queue_dir)
    write_registry_entries(specs, registry_dir_for(queue_dir))
    return paths


def registry_dir_for(queue_dir: Path) -> Path:
    """The hypothesis registry that belongs to a specs dir — its `11_hypotheses`
    sibling. Deriving it (rather than defaulting to the repo's) keeps a run
    against a throwaway specs dir, including any test, from writing into the
    real registry.
    """
    return Path(queue_dir).parent / "11_hypotheses"


def write_registry_entries(specs: list[dict], registry_dir: Path) -> list[Path]:
    """Write a companion hypothesis-registry entry for each proposed spec.

    The registry (cycle/11_hypotheses) is the outer collection that
    `ledger.search` and the viewer's Hypotheses tab iterate over, so a spec
    without an entry is invisible to both. Best-effort: a registry problem must
    never lose a spec that was already written to the queue.
    """
    from kilimanjaro.driver import registry

    written = []
    for spec in specs:
        try:
            entry = registry.build_entry(spec, signature=signature(spec))
            written.append(registry.write_entry(entry, registry_dir))
        except Exception as exc:  # noqa: BLE001 - queue write already succeeded
            print(
                f"warning: no registry entry for {spec.get('hypothesis_id')}: {exc}",
                file=sys.stderr,
            )
    return written
