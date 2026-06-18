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

Use `tools/ledger.py`, not manual edits:

```bash
# Append an event (event_id and created_at are generated)
python tools/ledger.py append \
  --hypothesis-id hyp-20260618-001 --event-type status_transition \
  --actor-type agent --actor-id agent-session-id \
  --from-status scan-ready --to-status scan-failed \
  --decision-reason failed-ic-threshold \
  --artifact-ids '{"scan_report_id": "scan-20260618-001"}'

# Search jointly across specs/ (family, mechanism, datasets, symbols) and
# this ledger (decision reasons, status) -- ledger events alone don't carry
# the descriptive fields, only the linked spec does.
python tools/ledger.py search --family order_book_imbalance --rejected-only
```

A duplicate can be marked with `--to-status duplicate --decision-reason
duplicate` and no `scan_report_id` in `--artifact-ids` — running a scan is
not required to record a duplicate.
