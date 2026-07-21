# Scanner loop queue

Drop scan-spec YAMLs here for `python src/cli.py loop` to process. Each is
validated, deduped against the ledger, scanned (train/holdout + verdicts), and
recorded. Survivors get a REVIEW_PACKET.md in their results folder.

Processing is idempotent — a spec whose (content, window) fingerprint is already
in the ledger is skipped, so re-running the loop is safe. The generator will drop
machine-generated specs here automatically.
