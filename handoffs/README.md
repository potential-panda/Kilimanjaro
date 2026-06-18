# Backtest Handoffs

Manual handoff packages for hypotheses that a human has approved for
Himalaya backtesting. Format defined by `../requirements/requirements.md`
(workflow stage 7) and `../requirements/data-contract.md`.

Naming:

- source of truth: `hyp-YYYYMMDD-NNN.yaml`
- generated human-readable view: `hyp-YYYYMMDD-NNN.md` (generated from the
  YAML, never hand-edited, never the source of truth)

A handoff file may only be generated once a hypothesis has reached
`backtest-approved` status in the ledger — generating it does not itself
perform that approval. It describes the entry/exit rules, parameter grid,
train/test/forward split windows, execution assumptions, and target
`potential-panda-core` strategy/variant pattern. It is a task description
for a human implementer, not generated strategy code.
