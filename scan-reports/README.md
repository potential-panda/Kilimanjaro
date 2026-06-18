# Scan Reports

One JSON file per signal scan, in the format defined by
`../requirements/data-contract.md` ("Scan Report Output").

Naming: `scan-YYYYMMDD-NNN.json`, where `NNN` is a zero-padded sequence
number unique within the date. A report's own `id` field must match its
filename. `hypothesis_id` links it back to the spec in `../specs/`.

A scan report is the full metric output for one hypothesis spec — IC, hit
rate, bucketed returns, decay across the full horizon grid, raw/effective
sample counts, and a significance confidence interval, computed against
`../requirements/requirements.md`'s "Deterministic Scanner Metrics". It is
attached to the hypothesis's ledger entry in `../ledger/`.

Generate one with `tools/scanner.py` (validate the spec first):

```bash
python tools/validate_spec.py specs/hyp-20260618-001.yaml
python tools/scanner.py --spec specs/hyp-20260618-001.yaml --start 2026-05-01 --end 2026-05-15
```

`threshold_verdict.status` is always `"not_evaluated"` in a freshly
generated report — applying the pass/fail thresholds is a separate step
(task 8), not something `scanner.py` does itself. v1 scope notes (see
`tools/scanner.py`'s module docstring): one feature source dataset and one
target source dataset, both bronze `OrderBook`; `target.type:
research_label` is not implemented; regime-bucket stability
(`stability_by_regime`) is always empty.
