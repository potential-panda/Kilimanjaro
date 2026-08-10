# cycle/ — the research-cycle data

Every durable data artifact the discovery loop reads or writes lives here,
numbered in pipeline order. Code and infrastructure (`doc/`, `src/`, `tests/`,
`tools/`, `web/`) stay at the repo root; `cycle/` is only the loop's data.

The number's tens digit is the phase:

**`0x` — foundation / reference inputs** (consumed throughout, rarely change):

| dir | what |
|-----|------|
| `01_schemas/`   | JSON Schemas the specs, ledger events, and reports validate against |
| `03_inventory/` | Makalu data-availability snapshots that gate what can be tested |

**`1x` — the loop pipeline, in order:**

The loop is a **cost-shaped funnel**: cheap wide screening first, expensive narrow
verdict last (full design: `doc/research-pipeline.md`).

| dir | loop step | metric | what |
|-----|-----------|--------|------|
| `11_hypotheses/`  | generate      | —           | the hypothesis registry — durable memory of every idea, including rejected/parked (`hyp-*.yaml`) |
| `12_specs/`       | queue         | —           | runnable scan configs (expression formulas + sweeps) — one flat dir, the loop's queue |
| `13_signal_corr/` | signal scan   | correlation | per-factor predictive power: each factor's IC (signal ↔ forward return) + the cross-factor correlation matrix. Ranks factors; does **not** hard-filter (14 owns inclusion) |
| `14_blend_corr/`  | blend scan    | correlation | weighted combinations of factors, ranked by blend IC. A strong single factor enters here as a **blend of one** |
| `15_return_eval/` | return eval   | return / trades | episode re-test (non-overlapping) on the **top blends only** — the expensive verdict. Grid-scan outputs (`grid_scan/{flip,expr,horizon}/…`) land here; large regenerable diagnostics are git-ignored |
| `16_calibration/` | calibrate     | scan↔Himalaya | fidelity of the fast scan vs the Himalaya backtest, per *mechanism* (cheap gate if the mechanism is already certified) |
| `17_ledger/`      | remember      | —           | append-only research memory (`events.jsonl`) joining to hypotheses by id |
| `18_handoffs/`    | promote       | —           | human-approved backtest handoff packages (`hyp-*.yaml`/`.md`, composite specs) |

Each subdirectory has its own `README.md` with the exact naming convention and
a pointer to the authoritative schema/contract under `doc/requirements/`.
