# Research Ledger

`events.jsonl` is Kilimanjaro's append-only research memory — the schema is
defined in `../requirements/data-contract.md` ("Ledger Event Contract").

Rules:

- One JSON object per line, append-only. Corrections are new events, not
  in-place edits.
- Every hypothesis that completes a spec gets an entry here, including
  rejected and parked ones — this is what lets a later session avoid
  re-proposing a dead idea.
- Search by family, mechanism keyword, dataset, symbol, or rejection reason
  before creating a new hypothesis (see `../requirements/hypothesis-contract.md`,
  "Duplicate Detection").
- Under version-control conflict, preserve both valid event lines and order
  by `created_at`; duplicate `event_id`s are invalid.

Events are written automatically by the grid scan tools
(`tools/grid_scan.py`, `tools/grid_scan_horizon.py`): every run appends one
`scan_attached` event to its hypothesis — default per tool / market config,
override with `--hypothesis-id`, escape hatch `--no-ledger` for throwaway
exploration — carrying the scan folder, train/holdout windows, number of
combos tested, and the rank-stable survivors. Grid runs never write status
transitions: promotion (`backtest-approved`) stays a human `human_approval`
event, and demotion/retirement stays a deliberate `status_transition`,
never a side effect of running a scan.

(The earlier `tools/scan_workflow.py` spec pipeline referenced here was
removed in the tools cleanup along with `validate_spec.py` /
`threshold_evaluator.py`; validate specs directly against
`schemas/hypothesis_spec.schema.json` for now.)

Lower-level operations remain available directly via `tools/ledger.py`:

```bash
# Append an event manually (event_id and created_at are generated) --
# e.g. a human_approval promoting a scan-passed hypothesis, or marking a
# duplicate without running a scan at all.
python tools/ledger.py append \
  --hypothesis-id hyp-20260618-001 --event-type human_approval \
  --actor-type human --actor-id lulurun \
  --from-status scan-passed --to-status backtest-approved \
  --decision-reason promotion-approved

# Search jointly across specs/ (family, mechanism, datasets, symbols) and
# this ledger (decision reasons, status) -- ledger events alone don't carry
# the descriptive fields, only the linked spec does.
python tools/ledger.py search --family order_book_imbalance --rejected-only
```

A duplicate can be marked with `--to-status duplicate --decision-reason
duplicate` and no `scan_report_id` in `--artifact-ids` — running a scan is
not required to record a duplicate.
