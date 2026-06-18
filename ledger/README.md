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

Don't append manually — run a spec through the full pipeline with
`tools/scan_workflow.py`, which validates, scans, evaluates thresholds,
and records exactly one event per run, with `from_status` always read from
the ledger's own latest state (not the spec file's static `status` field):

```bash
python tools/scan_workflow.py --spec specs/hyp-20260618-001.yaml --start 2026-05-01 --end 2026-05-15
```

This refuses outright (writes nothing) if the spec doesn't pass
`tools/validate_spec.py` first. `to_status` is whichever of
`scan-passed`/`scan-failed`/`needs-more-data`/`needs-tooling` the
threshold evaluator (`tools/threshold_evaluator.py`) decided — there is no
path from this workflow to `backtest-approved`; that's a separate, human
`human_approval` event (see below), never written automatically.

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
