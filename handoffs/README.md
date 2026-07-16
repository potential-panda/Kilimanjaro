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

## `build_backtest_env.py` — handoff → runnable Himalaya env

Turns an approved handoff's `himalaya_run_config` into a backtest env under
`~/env/backtest/<NAME>/`, so the frozen param set never has to be hand-copied
into JSON.

```bash
# Show what would be written, touch nothing:
python handoffs/build_backtest_env.py handoffs/hyp-20260713-002.yaml --dry-run

# Write the env config + param files:
python handoffs/build_backtest_env.py handoffs/hyp-20260713-002.yaml

# Write, then run the holdout window in Himalaya (--optimize combo):
python handoffs/build_backtest_env.py handoffs/hyp-20260713-002.yaml \
    --run --window holdout
```

Env resolution: a known target market maps to an existing env
(`GMO:BTC_JPY → QUINN_LEI` — see `KNOWN_ENVS`); on a known env only the config +
param files are rewritten and `trading.json` / `features.json` / `desc.json`
are left untouched. An unknown market gets a fresh env folder created from the
QUINN_LEI template (all seven files). Override the name with `--env NAME`.

Param translation (handoff → env): `sar_step → step`, `schedule →
schedule_version`, the `gate.*` block → the `rolling_dd_*` combo fields,
`markets` + `execution_size` → `config.json` (`market` / `ref_market` /
`order_size`). `acceleration` / `maximum` default to the grid-frozen `0.02` /
`0.2`. The combo is written to `params_combo.json` (run with `--optimize
combo`) and a single-point `param_grid.json`.

Running the backtest goes through Himalaya's `start.sh` — the same entry point
`Himalaya/doc/backtest.md` documents. `run_backtest(env, from_dt, to_dt,
optimize="combo")` is importable for scripted use. The tool never touches
ledger status: it consumes an already-approved handoff.
