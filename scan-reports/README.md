# Scan Reports

One JSON file per signal scan, in the format defined by
`../requirements/data-contract.md` ("Scan Report Output").

Naming: `scan-YYYYMMDD-NNN.json`, where `NNN` is a zero-padded sequence
number unique within the date. A report's own `id` field must match its
filename. `hypothesis_id` links it back to the spec in `../specs/`.

A scan report is the full metric/threshold output for one hypothesis spec —
IC, hit rate, bucketed returns, decay, sample counts, stability, and the
pass/fail verdict against `../requirements/requirements.md`'s
"Deterministic Scanner Metrics" thresholds. It is attached to the
hypothesis's ledger entry in `../ledger/`.
