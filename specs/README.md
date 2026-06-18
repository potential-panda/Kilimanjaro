# Hypothesis Specs

One YAML file per hypothesis, in the format defined by
`../requirements/hypothesis-contract.md`.

Naming:

- parent hypothesis: `hyp-YYYYMMDD-NNN.yaml`
- variant: `hyp-YYYYMMDD-NNN-vNN.yaml`

`NNN` is a zero-padded sequence number, unique within the date. A spec's
`hypothesis.id` field must match its filename.

Specs are the unit of work for the rest of the loop: nothing scans without a
valid spec here, and every spec gets a ledger entry in `../ledger/`
regardless of outcome.
