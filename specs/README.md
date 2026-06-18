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

Validate a spec before it's considered real:

```bash
python tools/validate_spec.py specs/hyp-20260618-001.yaml
```

This checks structural shape/enums against
`../schemas/hypothesis_spec.schema.json` and the cross-field rules from
`../requirements/hypothesis-contract.md`'s "Validation Rules" (missing
mechanism, duplicate search not performed, research-layer target without
version metadata, etc.). It does **not** check the feature expression
against the live SQL safety allowlist (task 6) or against Makalu's actual
data inventory (task 4) — those land once those tasks are implemented.
