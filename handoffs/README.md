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

## `build_backtest_env.py` — handoff → Himalaya combo list

Lists an approved handoff's `himalaya_run_config` combo(s) into an env's
`params_combo.json`, so the frozen param set never has to be hand-copied
into JSON.

A handoff only ever drives Himalaya's **combo** mode — it says *exactly which
param sets to run* — so the tool touches **exactly one file**:
`params_combo.json` in the target env. Everything else about the env
(`config.json` market/`order_size`, `trading.json`, `param_grid.json`,
`desc.json`) is the env's own setup and is left untouched. **The env must
already exist and be configured for backtesting** — the handoff supplies
combos, not env setup.

```bash
# Show what would be written, touch nothing:
python handoffs/build_backtest_env.py handoffs/hyp-20260713-002.yaml --dry-run

# Write params_combo.json:
python handoffs/build_backtest_env.py handoffs/hyp-20260713-002.yaml

# Write, then run the holdout window in Himalaya (--optimize combo):
python handoffs/build_backtest_env.py handoffs/hyp-20260713-002.yaml \
    --run --window holdout
```

Env resolution: the target market maps to a configured env via `KNOWN_ENVS`
(`GMO:BTC_JPY → QUINN_LEI`); override with `--env NAME`. An unmapped market
with no `--env` errors out (nothing to write into) rather than fabricating an
env. If the resolved env folder doesn't exist, the tool refuses — set the env
up first.

Param translation (handoff → env combo): `sar_step → step`, `schedule →
schedule_version`, the `gate.*` block → the `rolling_dd_*` combo fields.
`acceleration` / `maximum` default to the grid-frozen `0.02` / `0.2`. Accepts
either a single `combo` or a `combos` list under `himalaya_run_config`.

Note: `order_size` / execution size lives in the env's `config.json`, **not** in
a combo, so the tool does not set it — the run uses the env's configured size.
If a handoff needs a different size, adjust the env's `config.json` directly.

Running the backtest goes through Himalaya's `start.sh --optimize combo` — the
same entry point `Himalaya/doc/backtest.md` documents. `run_backtest(env,
from_dt, to_dt)` is importable for scripted use. The tool never touches ledger
status: it consumes an already-approved handoff.
