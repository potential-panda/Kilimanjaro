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


def _survivor_table(rows: list[dict]) -> str:
    if not rows:
        return "_none_\n"
    cols = ["expr", "threshold", "invert", "min_age", "max_age", "horizon", "max_hold_s"]
    head = "| " + " | ".join(cols + [
        "train_net", "train_t", "holdout_net", "holdout_t", "holdout_p_boot",
        "holdout_n", "holdout_pos_mo", "verdict"]) + " |\n"
    sep = "|" + "---|" * (len(cols) + 8) + "\n"
    body = ""
    for r in rows:
        params = " | ".join(str(r.get(c, "")) for c in cols)
        body += (f"| {params} | {_fmt(r.get('train_avg_net'))} | {_fmt(r.get('train_t_stat'),2)}"
                 f" | {_fmt(r.get('holdout_avg_net'))} | {_fmt(r.get('holdout_t_stat'),2)}"
                 f" | {_fmt(r.get('holdout_p_value_bootstrap'),4)}"
                 f" | {r.get('holdout_n')} | {r.get('holdout_pos_month_share')}"
                 f" | {r.get('verdict')} |\n")
    return head + sep + body


def _yellow_flags(res: dict, outcome: Outcome) -> list[str]:
    flags = []
    counts = outcome.verdicts
    if counts.get("regime_dependent"):
        flags.append(f"{counts['regime_dependent']} regime_dependent combos in the grid "
                     "— edge may be regime-specific.")
    if counts.get("decayed"):
        flags.append(f"{counts['decayed']} decayed combos — watch for OOS erosion.")
    dropped = outcome.n_stable - outcome.n_survivors
    if dropped:
        flags.append(f"{dropped} rank-stable combo(s) were dropped by the FDR correction "
                     "— they did not clear multiple-testing.")
    best = outcome.best or {}
    hm = best.get("holdout_pos_month_share")
    if isinstance(hm, (int, float)) and hm < 0.6:
        flags.append(f"best survivor holdout monthly consistency low (pos_month_share={hm}).")
    if outcome.n_survivors == 1:
        flags.append("only one confirmed survivor — thin plateau, confirm it is not a fluke.")
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
    survivors = [r for r in (res.get("stable") or []) if r.get("mt_significant")]
    p = res["params"]

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
- selection: rank stability (top {p['top_quantile']:.0%} of both windows, min_n={p['min_n']})
  AND Benjamini-Hochberg FDR at alpha={p.get('mt_alpha')} on holdout bootstrap p
- cost charged: **{p['costs_bps']:.2f} bps** round-trip (target spread, mid-to-mid screen)
- rank-stable: {outcome.n_stable} | confirmed after FDR: {outcome.n_survivors}
- verdict mix: {outcome.verdicts}
- fingerprint: `{fingerprint}`
- scan folder: `{out_dir}`

## Result
**{outcome.headline}**

### Confirmed survivors (rank-stable AND FDR-significant)
{_survivor_table(survivors)}

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
