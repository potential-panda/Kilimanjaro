# Backtest handoffs

Manual packages for discovery candidates approved by a human for independent
implementation or replay. Formats are defined by
`../../doc/requirements/data-contract.md`.

A handoff is evidence and a task description, not generated strategy code. It
must freeze the signal definition, parameters, data windows, execution
assumptions, and acceptance criteria. Multi-file packages live in a named
subdirectory.
