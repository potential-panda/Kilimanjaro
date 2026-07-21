# cycle/ — the research-cycle data

Every durable data artifact the discovery loop reads or writes lives here,
numbered in pipeline order. Code and infrastructure (`doc/`, `src/`, `tests/`,
`tools/`, `web/`) stay at the repo root; `cycle/` is only the loop's data.

The number's tens digit is the phase:

**`0x` — foundation / reference inputs** (consumed throughout, rarely change):

| dir | what |
|-----|------|
| `01_schemas/`   | JSON Schemas the specs, ledger events, and reports validate against |
| `02_markets/`   | market universe + precomputed volatility/spread/lead-lag "wave" data (regenerable; git-ignored) |
| `03_inventory/` | Makalu data-availability snapshots that gate what can be tested |

**`1x` — the loop pipeline, in order:**

| dir | loop step | what |
|-----|-----------|------|
| `11_hypotheses/` | generate  | the hypothesis registry — durable memory of every idea, including rejected/parked (`hyp-*.yaml`) |
| `12_specs/`      | scan      | runnable scan configs (expression formulas + sweeps) + the Stage-A `queue/` |
| `13_results/`    | report    | grid-scan outputs (`grid_scan/{flip,expr,horizon}/…`) |
| `14_ledger/`     | remember  | append-only research memory (`events.jsonl`) joining to hypotheses by id |
| `15_handoffs/`   | promote   | human-approved backtest handoff packages (`hyp-*.yaml`/`.md`) |

Each subdirectory has its own `README.md` with the exact naming convention and
a pointer to the authoritative schema/contract under `doc/requirements/`.
