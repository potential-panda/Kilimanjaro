# Hypothesis Specs

One YAML file per hypothesis, in the format defined by
`../../doc/requirements/hypothesis-contract.md`.

Naming:

- parent hypothesis: `hyp-YYYYMMDD-NNN.yaml`
- variant: `hyp-YYYYMMDD-NNN-vNN.yaml`

`NNN` is a zero-padded sequence number, unique within the date. A spec's
`hypothesis.id` field must match its filename.

This directory is the **hypothesis registry** — the durable memory of every idea
tried, including rejected and parked ones. The ledger (`../17_ledger/`) joins to it
by `hypothesis_id` for duplicate-detection search and for the viewer's Hypotheses
tab, so entries stay even after an idea is retired: that is what lets a later
session avoid re-proposing a dead idea. The `status:` field inside a file is its
*initial* status; the **current** status is whatever the ledger last recorded.

Two spec formats, two purposes:

- **`11_hypotheses/`** (here) — hypothesis-contract specs: the human-readable
  registry above. Validate against `../01_schemas/hypothesis_spec.schema.json` and
  the cross-field rules in `hypothesis-contract.md` ("Validation Rules": missing
  mechanism, duplicate search not performed, research-layer target without
  version metadata, …).
- **`../12_specs/`** — the loop's working *scan* specs (expression formulas +
  sweep dimensions) that `cli.py grid expr` / `cli.py loop` / the factory cadence
  consume. Simpler format; validated by
  `driver/validate.validate_scan_spec` (structural shape + AST-safe expression).
