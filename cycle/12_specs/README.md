# Scan specs — the loop's queue

One flat directory of scan-spec YAMLs. `python src/cli.py loop` (and the
`factory` cadence) processes every `*.yaml` here: validated, deduped against the
ledger, scanned (train/holdout + verdicts), and recorded. Survivors get a
`REVIEW_PACKET.md` in their results folder.

These are scan *configurations*, not hypotheses. Every spec names its hypothesis
via `hypothesis_id:`, and the idea itself — title, mechanism, falsification
tests, decision trail — lives in the registry at `../11_hypotheses/hyp-*.yaml`.
One hypothesis may have several specs (different markets, or a different
`return_calc`); that is the normal 1:N relationship, not duplication.

Processing is idempotent — a spec whose (content, window) fingerprint is already
in the ledger is skipped, so re-running the loop is safe.

## Naming

| kind | filename | written by |
|------|----------|------------|
| generated | `hyp-YYYYMMDD-NNN_<family>_<target>.yaml` | `cli.py generate` |
| hand-written | `hyp-YYYYMMDD-manual_<name>.yaml` | a human |

The `manual_` infix marks specs a person authored. They matter because the
generator can only emit what `driver/generate.py:FAMILIES` encodes, at
`return_calc: fixed_horizon` — so mechanisms like `episode`, and any idea outside
those families, can *only* arrive by hand. Keep that path open.

Note the filename prefix is not the hypothesis id: ids are `hyp-YYYYMMDD-NNN`
(schema-constrained), and several manual specs may share one. The id inside the
file is authoritative; the filename only names the results folder.

```bash
# run one spec directly, outside the loop
python src/cli.py grid expr --spec cycle/12_specs/hyp-20260714-manual_basis_eth.yaml \
    --start 2025-07-01 --end 2026-06-30 --split 2026-03-01
```
