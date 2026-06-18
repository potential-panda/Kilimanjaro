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
