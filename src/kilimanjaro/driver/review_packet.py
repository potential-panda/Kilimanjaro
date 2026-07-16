"""Draft a human review packet for a scan-passed hypothesis.

Per doc/requirements/agent-loop-design.md, a scan-to-backtest packet is what a
human reads to decide whether to spend a Himalaya backtest. The driver never
promotes; it parks survivors here at the human gate. The packet is written into
the scan's own output folder so evidence and review travel together.
"""

from __future__ import annotations

from pathlib import Path

from kilimanjaro.driver.policy import Outcome


def _fmt(v, nd=3):
    try:
        return f"{float(v):+.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def _survivor_table(stable: list[dict]) -> str:
    if not stable:
        return "_none_\n"
    cols = ["expr", "threshold", "invert", "min_age", "max_age", "horizon", "max_hold_s"]
    head = "| " + " | ".join(cols + [
        "train_net", "train_t", "holdout_net", "holdout_t", "holdout_n",
        "train_pos_mo", "holdout_pos_mo", "verdict"]) + " |\n"
    sep = "|" + "---|" * (len(cols) + 8) + "\n"
    body = ""
    for r in stable:
        params = " | ".join(str(r.get(c, "")) for c in cols)
        body += (f"| {params} | {_fmt(r.get('train_avg_net'))} | {_fmt(r.get('train_t_stat'),2)}"
                 f" | {_fmt(r.get('holdout_avg_net'))} | {_fmt(r.get('holdout_t_stat'),2)}"
                 f" | {r.get('holdout_n')} | {r.get('train_pos_month_share')}"
                 f" | {r.get('holdout_pos_month_share')} | {r.get('verdict')} |\n")
    return head + sep + body


def _yellow_flags(res: dict, outcome: Outcome) -> list[str]:
    flags = []
    counts = outcome.verdicts
    if counts.get("regime_dependent"):
        flags.append(f"{counts['regime_dependent']} regime_dependent combos in the grid "
                     "— edge may be regime-specific.")
    if counts.get("decayed"):
        flags.append(f"{counts['decayed']} decayed combos — watch for OOS erosion.")
    best = outcome.best or {}
    hm = best.get("holdout_pos_month_share")
    if isinstance(hm, (int, float)) and hm < 0.6:
        flags.append(f"best combo holdout monthly consistency low (pos_month_share={hm}).")
    if outcome.n_stable == 1:
        flags.append("only one rank-stable combo — thin plateau, confirm it is not a fluke.")
    return flags


def write_review_packet(
    spec: dict, res: dict, outcome: Outcome, out_dir: Path,
    start: str, end: str, fingerprint: str,
) -> Path:
    """Write REVIEW_PACKET.md into out_dir; return its path."""
    hyp = spec.get("hypothesis_id", "?")
    t = spec["target"]
    markets = ", ".join(f"{a}={m['exchange']}/{m['symbol']}" for a, m in spec["markets"].items())
    exprs = "\n".join(f"  - `{e.get('label', e['expr'])}`: `{e['expr']}` "
                      f"invert={e.get('invert', [False])}" for e in spec["expressions"])
    flags = _yellow_flags(res, outcome)

    md = f"""# Review packet — {hyp} (scan-passed)

**Recommended decision:** human review for Himalaya backtest approval.
This is a scan survivor, NOT an approval — promotion is your call.

## Hypothesis
- id: `{hyp}`
- return_calc: `{spec.get('return_calc', 'fixed_horizon')}`
- signal markets: {markets}
- target (execution): {t['exchange']}/{t['symbol']} (size {t['execution_size']})
- expressions:
{exprs}

## Scan
- window: train/holdout split of {start} .. {end}  (windows: {res['windows']})
- combos tested: {outcome.n_combos}
- selection: rank stability, top {res['params']['top_quantile']:.0%} of both windows,
  min_n={res['params']['min_n']}, costs={res['params']['costs_bps']} bps
- verdict mix: {outcome.verdicts}
- fingerprint: `{fingerprint}`
- scan folder: `{out_dir}`

## Result
**{outcome.headline}**

### Rank-stable survivors
{_survivor_table(res.get('stable') or [])}

## Yellow flags
{chr(10).join('- ' + f for f in flags) if flags else '- none'}

## What a human decides here
1. Approve for Himalaya backtest -> record human_approval (scan-passed -> backtest-approved),
   then a handoff YAML is drafted.
2. Reject -> retire with a reason.
3. Request a variant (changed horizon/threshold/regime) -> new spec, new hypothesis id.

Reminder: this survivor is mid-to-mid + flat-cost unless run_calc is `episode`.
If it is fixed_horizon, confirm it survives real bid/ask execution (an episode
re-test) before trusting the net numbers.
"""
    path = out_dir / "REVIEW_PACKET.md"
    path.write_text(md)
    return path
