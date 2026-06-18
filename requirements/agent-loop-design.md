# Agent Loop Design

## Overview

Kilimanjaro coordinates two loops:

- scanner loop: cheap, frequent, mostly agent-autonomous stages 1-6;
- backtest loop: expensive, human-gated stages 7-9.

The agent may reject or park ideas autonomously, but cannot spend Himalaya backtest resources or promote strategies without human approval.

## Roles

### Research Agent

Responsibilities:

- refresh Makalu data inventory;
- search ledger for duplicates;
- draft structured hypothesis specs;
- validate specs;
- run signal scans when data and tooling allow;
- interpret scan outputs against thresholds;
- update ledger for all outcomes;
- draft human review packets for `scan-passed` ideas and handoff packages only after `backtest-approved`.

### Human Reviewer

Responsibilities:

- approve or reject scan-to-backtest promotion;
- approve or reject backtest-to-strategy-library promotion;
- approve taxonomy or threshold changes;
- audit autonomous scanner loop output.

### Engineering Implementer

Responsibilities:

- convert approved handoff packages into potential-panda-core strategy variants;
- create Himalaya run configs and parameter grids;
- run or supervise backtests;
- provide result artifacts back to Kilimanjaro.

## State Transitions

```text
draft
  -> invalid-spec
  -> duplicate
  -> needs-more-data
  -> needs-tooling
  -> scan-ready

scan-ready
  -> scan-failed
  -> scan-passed

scan-passed
  -> retired                 # human rejects backtest spend
  -> backtest-approved       # human approves handoff creation

backtest-approved
  -> backtest-running
  -> retired                 # implementation infeasible

backtest-running
  -> retired                 # failed backtest protocol
  -> backtest-passed

backtest-passed
  -> awaiting-promotion-decision

awaiting-promotion-decision
  -> promoted
  -> retired
```

## Enforceable Transition Matrix

| From | To | Actor | Required Artifacts / Fields |
|---|---|---|---|
| none | draft | agent or human | valid hypothesis id, spec file path |
| draft | invalid-spec | agent | validation report with failed fields |
| draft | duplicate | agent | duplicate target id and duplicate-search summary |
| draft | needs-more-data | agent | inventory snapshot id and missing data fields |
| draft | needs-tooling | agent | unsupported feature/tooling reason |
| draft | scan-ready | agent | valid spec id, inventory snapshot id, duplicate-search summary |
| invalid-spec | draft | agent or human | revised spec revision id |
| needs-more-data | draft | agent or human | new inventory snapshot or changed data requirements |
| needs-tooling | draft | human | tooling availability note or revised SQL-expressible spec |
| duplicate | retired | agent | duplicate target id |
| scan-ready | scan-failed | agent | scan report id, failed threshold rules |
| scan-ready | scan-passed | agent | scan report id, threshold evaluator result, no blocking metadata gaps |
| scan-passed | retired | human | review decision event id and rejection reason |
| scan-passed | backtest-approved | human | approval event id, review packet id, waiver ids if any |
| backtest-approved | backtest-running | agent or human | handoff YAML id and external run reference or implementation task id |
| backtest-approved | retired | human | implementation infeasibility reason |
| backtest-running | retired | agent or human | backtest result summary and failed protocol rules |
| backtest-running | backtest-passed | agent | backtest result summary, split metrics, no blocking protocol failures |
| backtest-passed | awaiting-promotion-decision | agent | promotion review packet id |
| awaiting-promotion-decision | promoted | human | promotion approval event id |
| awaiting-promotion-decision | retired | human | promotion decline event id |

`retired`, `promoted`, and `duplicate` are terminal for that hypothesis id. Further work requires a variant or new parent id.

Review packet generation follows `scan-passed`. Handoff YAML generation follows `backtest-approved`; it does not itself approve a hypothesis. If handoff generation fails, status remains `backtest-approved` until a human retires it or the handoff is corrected.

## Approval And Waiver Event Schema

Approval and waiver records are JSON Lines events in `ledger/events.jsonl`:

```json
{
  "event_id": "evt-20260618-001",
  "event_type": "human_approval",
  "hypothesis_id": "hyp-20260618-001",
  "actor_type": "human",
  "actor_id": "reviewer-name-or-handle",
  "created_at": "2026-06-18T00:00:00Z",
  "approved_transition": {"from": "scan-passed", "to": "backtest-approved"},
  "review_packet_id": "review-20260618-001",
  "decision": "approved",
  "waiver_ids": [],
  "notes": ""
}
```

Waiver event:

```json
{
  "event_id": "evt-20260618-002",
  "event_type": "waiver",
  "hypothesis_id": "hyp-20260618-001",
  "actor_type": "human",
  "actor_id": "reviewer-name-or-handle",
  "created_at": "2026-06-18T00:00:00Z",
  "waived_rule": "minimum_distinct_days",
  "threshold_value": 5,
  "observed_value": 4,
  "scope": "scan_to_backtest",
  "reason": "",
  "expires_at": null
}
```

Waivable thresholds:

- minimum distinct days;
- raw/effective sample count for lower-frequency scans;
- regime bucket coverage when the hypothesis explicitly targets one regime.

Non-waivable blockers:

- missing mechanism;
- missing falsification test;
- unsupported arbitrary Python transform;
- look-ahead leakage;
- missing required partition metadata;
- missing research `param_id`;
- missing lead-lag timestamp alignment;
- missing execution cost assumptions.

## Scanner Loop

1. Refresh inventory.
2. Select or draft hypothesis.
3. Search ledger for duplicates and prior failures.
4. Validate spec.
5. If invalid, write `invalid-spec` ledger decision.
6. If data missing, write `needs-more-data`.
7. If feature requires unsupported transform, write `needs-tooling`.
8. Run scanner.
9. Attach scan report to ledger.
10. Apply thresholds.
11. Write `scan-failed` or `scan-passed`.
12. For `scan-passed`, prepare human review packet. Generate handoff YAML only after human approval transitions the entry to `backtest-approved`.

## Human Review Packet

Every scan-to-backtest packet must include:

- hypothesis spec;
- duplicate-search summary;
- inventory snapshot reference;
- scan report;
- full horizon/threshold grid;
- stability by day/symbol/regime;
- cost buffer calculation;
- yellow flags;
- recommended decision: approve backtest, reject, or request revision.

## Backtest Handoff Packet

For `backtest-approved` hypotheses, Kilimanjaro drafts:

```yaml
handoff:
  hypothesis_id: hyp-20260618-001
  scan_report_id: scan-20260618-001
  intended_strategy_family: ""
  potential_panda_core_mapping:
    base_contract: "qate.trading.strategy"
    example_variant_reference: ""
    open_questions: []
  himalaya_run_config:
    markets: []
    train_window: {}
    test_window: {}
    forward_window: {}
    parameter_grid: []
    execution_model:
      fees_bps: null
      slippage_bps: null
      latency_ms: null
      execution_size: null
  expected_outputs:
    - net_pnl_after_costs
    - trade_count
    - turnover
    - win_rate
    - sharpe_or_equivalent
    - max_drawdown
    - capacity_sensitivity
    - parameter_surface
    - regime_breakdown
```

## Retry And Revision Loops

Allowed revisions:

- fix invalid spec fields before scan;
- park and retry after additional Makalu data appears;
- create variant with explicitly changed horizon/threshold/regime after prior result;
- create a new hypothesis when forward-test retuning would otherwise leak into the same entry.

Disallowed revisions:

- changing primary horizon after seeing scan results without creating a variant;
- hiding failed grid cells;
- rerunning a failed forward window with new parameters under the same hypothesis;
- promoting a scan that lacks cost or latency assumptions;
- converting a `needs-tooling` idea into arbitrary Python execution in v1.

## Audit Controls

- First 5 autonomous runs for each taxonomy family require human review before unsupervised volume.
- Weekly audit reviews at least 10% of rejected entries and 100% of scan-passed entries.
- Audit checks threshold application, stale research labels, and look-ahead/leakage.
- Confirmed scanner/threshold bugs halt affected autonomous scope until impacted entries are rechecked.

## Persisted Artifacts

Kilimanjaro must persist:

- inventory snapshots or references;
- hypothesis specs;
- duplicate-search summaries;
- scan configs;
- scan reports;
- ledger decisions;
- human review decisions;
- handoff packets;
- backtest result summaries or links;
- threshold/taxonomy change records.

## Interface Surfaces

Expected v1 surfaces:

- CLI command or script for inventory refresh;
- CLI command or script for spec validation;
- CLI command or script for duplicate search;
- CLI command or script for signal scan;
- file-based registry/ledger under version control;
- YAML handoff packet for Himalaya implementation, with optional generated Markdown review view.

No UI is required for v1.
