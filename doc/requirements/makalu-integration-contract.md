# Makalu Integration Contract

## Purpose

This contract defines the exact Makalu metadata and data-discovery behavior Kilimanjaro v1 may rely on. Any metadata not available through the commands below is treated as missing; exploratory scans may run, but promotion to backtest is blocked until the metadata is supplied or waived by a human where allowed.

## Bronze-First Reliability Model

This is the governing principle for everything else in this contract, confirmed against the real Makalu repo and data during the implementation-feasibility spike (see "Known Gap" below for the concrete finding that motivated it.)

- **Bronze (`Trade`, `OrderBook`) is the only layer Kilimanjaro may assume is reliably present and queryable.** It is the raw capture; nothing about it depends on a derived pipeline having run correctly.
- **Every other layer (silver, gold, feature, research) is a candidate, not a dependency.** Each existing derived dataset (`BookState`, `BookChange`, `MarketPrice`, `VolSpread`, `LeadLag`, `Feature_Mid`, `Feature_NearHFT`, the research labels) is something that *may* already be populated and *may* already be valuable for a given hypothesis — or may not be either. Kilimanjaro must not hard-depend on any specific derived dataset existing, being populated for the needed exchange/symbol, or having been validated as a useful signal. None of that is assumed; it's checked per spec.
- **`catalog.yaml`'s declared `layer` and other metadata are a hint, not a guarantee of actual queryability.** They can drift from the real on-disk storage location (see "Known Gap"). Kilimanjaro must independently verify a non-bronze dataset is actually registered and queryable (`query --list` includes it, or a `DESCRIBE <view>` succeeds) before depending on it for a scan — not just check that the catalog entry exists.
- **When a hypothesis needs a derived dataset/column that isn't currently usable, that is not a blocker — it's a routing decision**, in this priority order:
  1. **Populate it.** If the dataset is a known Makalu pipeline output (it has a `populate` subcommand, e.g. `populate bookstate`, `populate bookchange`, `populate market-price`, `populate volspread`, `populate leadlag`) but is missing for the needed exchange/symbol/date range, run Makalu's existing populate command for those partitions. This is normal use of Makalu's framework, not a new pipeline.
  2. **Derive it fresh from bronze instead.** If populating isn't worthwhile (e.g. the hypothesis only needs one exchange/symbol/date range once, or the existing derived dataset's definition doesn't quite match what the hypothesis needs), compute the needed feature directly from `Trade`/`OrderBook` inside the scanner's own feature-expression layer. Bronze is always sufficient to derive any of the documented families' raw mechanics (imbalance, microprice, pull/refill, aggression) — the existing silver/gold datasets are a precomputed convenience, not the only path to that signal.
  3. **Define a new derived dataset, following Makalu's existing layer/pipeline conventions.** If a hypothesis needs a genuinely new derived signal that doesn't exist as any Makalu pipeline output, it is acceptable to add one — partitioned and stored the same way as existing silver/gold/feature datasets, implemented using Makalu's existing `feature_engineering`/pipeline code patterns as precedent, and registered in `catalog.yaml` with a `layer` that actually matches where it's stored on disk. This extends Makalu's own framework using its own idioms; it is not "duplicating Makalu" (the non-goal in `requirements.md` is about not building a parallel, competing data system — adding one more dataset through Makalu's established pipeline pattern is the opposite of that).
  4. **If a populated, queryable dataset turns out not to carry signal once scanned, that's just a normal scan outcome** (`scan-failed`/`rejected`), not a data problem. Existing derived datasets earn their place by passing scans, not by being assumed valuable because they already exist.
- A hypothesis is never blocked because a *specific* derived dataset is unpopulated, unqueryable, or has gone unused — it is only blocked if bronze itself can't support the needed window/coverage, since bronze has no fallback.

## Known Gap (fixed upstream 2026-06-21): catalog-declared layer disagreed with actual storage

Originally confirmed directly against the running Makalu instance (2026-06-18), and confirmed fixed by re-checking the live instance on 2026-06-21:

- `catalog.yaml` used to declare `layer: bronze` for both `BookState` and `BookChange`, while the actual data lived on disk under `silver/`. Makalu's query engine builds each dataset's view-registration path from the catalog's declared `layer`, so the mismatch meant `query --sql "DESCRIBE bookstate"` failed with "table does not exist" even though the dataset had real, populated data.
- **As of 2026-06-21, this is fixed**: `catalog.yaml` now correctly declares `layer: silver` for both, and `tools/inventory.py`'s live snapshot confirms `query_verified: true` for both — Makalu's query CLI registers them correctly now.
- `Feature_Mid` still has no populated data anywhere (only `Feature_NearHFT` exists on disk) — that's an unrelated, still-true non-population gap, not a layer mismatch.

Per the Bronze-First Reliability Model above, this never blocked v1 even while the mismatch existed — Kilimanjaro independently verifies queryability rather than trusting the catalog (see "Catalog Contract" below), and the same imbalance/pull-refill mechanics remain available to derive directly from bronze `OrderBook` regardless of whether `BookState`/`BookChange` are populated for a given market.

### Known populate commands (confirmed against `python cli.py populate --help`, 2026-06-18)

Only these datasets have a known, existing Makalu pipeline that Kilimanjaro
can invoke for the `populate` routing decision:

| Dataset | Populate subcommand |
|---|---|
| `Trade`, `OrderBook` | `populate bronze` (always together, from raw msgpack) |
| `BookChange` | `populate BookChange` |
| `BookState` | `populate BookState` |
| `TradeAggression` | `populate TradeAggression` |
| `MarketPrice` | `populate MarketPrice` |
| `VolSpread` | `populate VolSpread` (depends on `MarketPrice`) |
| `LeadLag` | `populate LeadLag` (depends on `MarketPrice` for both markets) |

`Feature_Mid`, `Feature_NearHFT`, `TripleBarrierLabel`,
`TripleBarrierLabel_tmp`, `BuyLabel_tmp`, and `BacktestPnL` have **no**
populate subcommand in the current Makalu CLI — whatever data exists for
them today (only `Feature_NearHFT` does) was produced by a process outside
this CLI surface. For these, when unpopulated for a needed scope, the
Bronze-First routing defaults to `derive_from_bronze` rather than
`populate`, since no Kilimanjaro-invokable pipeline exists; `new_dataset_needed`
remains available if deriving from bronze genuinely isn't sufficient.

## Required Makalu Sources

Kilimanjaro v1 reads these Makalu files and commands:

- catalog: `/home/lulurun/workspace/Makalu/doc/catalog.yaml`
- column definitions: `/home/lulurun/workspace/Makalu/doc/dataset_definitions.md`
- query docs: `/home/lulurun/workspace/Makalu/doc/querying.md`
- query CLI: `python /home/lulurun/workspace/Makalu/src/cli.py query`
- data availability CLI where supported: `python /home/lulurun/workspace/Makalu/src/cli.py data-check`

Kilimanjaro must treat these as read-only.

## Catalog Contract

`doc/catalog.yaml` is the source for:

- `dataset_name`
- `layer`
- `description`
- `grain`
- `primary_timestamp`
- `partition_columns`
- `source_dataset`
- `path_template`

`layer` determines canonicality:

- `bronze`, `silver`, `gold`, `feature` -> `canonical`
- `research` -> `research-ephemeral`

If `layer`, `primary_timestamp`, `partition_columns`, or `path_template` is missing for a dataset, Kilimanjaro must mark the hypothesis `needs-tooling`, set the related metadata field to `missing`, and block promotion for hypotheses depending on it.

**Catalog `layer` is not trusted blindly for non-bronze datasets** (see "Known Gap" above). For any dataset other than `Trade`/`OrderBook`, Kilimanjaro must independently confirm the dataset is actually registered and queryable — via `query --list` containing it, or `DESCRIBE <view_name>` succeeding — before treating the catalog's declared layer as accurate. If the dataset is absent from `query --list` despite a catalog entry, record `availability_probe_layer` using `data-check`'s (or a direct filesystem probe's) resolved layer instead of the catalog's, and route through the Bronze-First populate/derive/build decision above rather than blocking.

## Query CLI Contract

### List Datasets

Command:

```bash
python /home/lulurun/workspace/Makalu/src/cli.py query --list
```

Actual JSON shape, confirmed against the running CLI (2026-06-18) — corrected from an earlier assumed shape that did not match:

```json
{
  "datasets": [
    {
      "dataset": "Trade",
      "layer": "bronze",
      "grain": "per trade",
      "partitions": "exchange, symbol",
      "source": ""
    }
  ]
}
```

There is no `dataset_name`, `view_name`, or `description` field. `source` lists the upstream dataset(s) it was derived from (empty for bronze).

Implementation rule:

- The queryable view name is not returned by `--list`; derive it as lowercase `dataset` (confirmed correct for every dataset actually returned, e.g. `Trade` -> `trade`).
- **Only datasets that have at least one populated partition on disk, under the path implied by their (possibly wrong — see "Known Gap" above) catalog `layer`, appear in this list at all.** Absence from `--list` does not mean the dataset doesn't exist in the catalog or can't be populated — confirm against `catalog.yaml` and route through the Bronze-First decision above before treating it as unavailable.
- If `query --list` returns `{"error": "..."}`, inventory refresh fails.

### Arbitrary Read Query

Command shape:

```bash
python /home/lulurun/workspace/Makalu/src/cli.py query --sql "<SELECT ...>"
```

Expected JSON shape:

```json
{
  "row_count": 123,
  "columns": ["ts", "mid"],
  "rows": [{"ts": 1770000000.0, "mid": 100.0}],
  "truncated": true,
  "note": "Showing first 200 of 1000 rows. Add LIMIT to your query."
}
```

Error shape:

```json
{"error": "message"}
```

Rules:

- Kilimanjaro-generated queries must be `SELECT` only.
- Every data query must filter by required partition columns and date range.
- Query output is capped to 200 rows for returned JSON; scanner implementations must use appropriate aggregation or direct engine integration if full result extraction is needed. The CLI JSON cap makes the CLI suitable for metadata probes and small validation queries, not high-volume scan extraction unless Makalu adds an uncapped export mode.

## Column Metadata Contract

Preferred source:

```sql
DESCRIBE <view_name>
```

via:

```bash
python /home/lulurun/workspace/Makalu/src/cli.py query --sql "DESCRIBE bookstate"
```

Expected JSON shape follows the query contract with `columns` and `rows`.

Fallback source:

- parse `/home/lulurun/workspace/Makalu/doc/dataset_definitions.md` for documented columns and column patterns.

Rules:

- Scanner validation may accept pattern-defined columns such as `vol_imb_k{k}` only after resolving the concrete column name through `DESCRIBE` or a sample query.
- If column existence cannot be proven, spec validation fails with `missing-column-metadata`.

## Availability Probe Contract

### Preferred for Supported Datasets: `data-check`

Command shape:

```bash
python /home/lulurun/workspace/Makalu/src/cli.py data-check \
  --dataset Trade --exchange GMO --symbol BTC_JPY \
  --start 2026-01-01 --end 2026-01-31
```

For partitioned datasets, include required partitions:

```bash
python /home/lulurun/workspace/Makalu/src/cli.py data-check \
  --dataset MarketPrice --exchange GMO --symbol BTC_JPY \
  --execution-size 0.002 --start 2026-01-01 --end 2026-01-31
```

Actual text shape when all partitions are supplied, confirmed against the
running CLI (2026-06-18) — corrected from the originally assumed shape,
which used an ASCII arrow and always listed missing dates individually:

```text
Dataset : Trade
Location: /panda/makalu-data/bronze/dataset=Trade/exchange=GMO/symbol=BTC_JPY
Partition: exchange=GMO / symbol=BTC_JPY
Min date: 2025-06-29
Max date: 2026-06-10
Total   : 340 days on disk
Window  : 2026-01-01 → 2026-01-31
Missing : 0 days
```

The arrow on the `Window` line is the Unicode character `→` (U+2192), not
`->`. When a window has missing dates, they are listed below the `Missing`
line — each as either a single `YYYY-MM-DD` or a `YYYY-MM-DD → YYYY-MM-DD`
range, never expanded day-by-day:

```text
Missing : 5 days
  2025-12-25 → 2025-12-29
```

When `--start`/`--end` are omitted, `Missing` covers the full on-disk range
instead and the line gets a suffix: `Missing : 0 days (between min and
max)` (no `Window` line is printed in that case).

Expected text shape when a partition value is omitted:

```text
Available exchange:
  GMO
  BINANCE
```

Rules:

- v1 must parse the exact labels above: `Dataset`, `Location`, `Partition`, `Min date`, `Max date`, `Total`, `Window`, `Missing`, and `Available <key>`.
- The parser must accept both a single date and a `start → end` range on
  indented lines following `Missing`, and must not assume `Window` is
  always present (it's omitted when no `--start`/`--end` was given).
- If output contains warnings or cannot be parsed, record `availability_status: unknown` and block promotion.
- Current `data-check` support is limited to the datasets in Makalu `src/makalu/cli/common.py`; it does not cover feature and research datasets today.

### Fallback for Feature/Research Datasets

When `data-check` does not support a dataset, use catalog `path_template` plus filesystem existence checks for all required partitions/date values.

Required output shape in Kilimanjaro inventory:

```yaml
availability_probe:
  method: path_template_filesystem
  path_template: "/panda/makalu-data/feature/dataset=Feature_Mid/exchange={exchange}/symbol={symbol}/date={date}/data.parquet"
  checked_dates: ["2026-01-01"]
  existing_dates: ["2026-01-01"]
  missing_dates: []
```

If the runtime cannot access `/panda/makalu-data`, mark `availability_status: unknown` and block promotion.

## Partition Discovery

Use `data-check` progressive disclosure where supported:

```bash
python /home/lulurun/workspace/Makalu/src/cli.py data-check --dataset Trade
python /home/lulurun/workspace/Makalu/src/cli.py data-check --dataset Trade --exchange GMO
```

For SQL-discoverable partitions, use `SELECT DISTINCT`:

```sql
SELECT DISTINCT execution_size
FROM marketprice
WHERE exchange='GMO' AND symbol='BTC_JPY'
LIMIT 50
```

```sql
SELECT DISTINCT param_id
FROM volspread
WHERE exchange='GMO' AND symbol='BTC_JPY' AND execution_size='0.002'
LIMIT 50
```

Rules:

- Required partition columns come from catalog `partition_columns`.
- `MarketPrice` requires `execution_size`.
- `VolSpread` requires `execution_size` and `param_id`.
- `Chart_*` requires `param_id`.
- `LeadLag` requires `market_a`, `market_b`, and `param_id`.
- Research labels require `param_id`.

## Research-Layer Metadata

For `TripleBarrierLabel`, `TripleBarrierLabel_tmp`, and `BuyLabel_tmp`, Kilimanjaro must record:

```yaml
research_metadata:
  dataset: TripleBarrierLabel
  param_id: ""
  build_id: null
  generated_at: null
  label_assumptions:
    timeout_sec: null
    barrier_multiplier: null
    pt_multiplier: null
    sl_multiplier: null
    source_description: "Makalu dataset_definitions.md and param_id"
  metadata_status: complete|partial|missing
```

Known v1 limitation:

- Makalu catalog documents `param_id` and label semantics, but the reviewed docs do not expose a guaranteed `build_id` or `generated_at` field.

Deterministic rule:

- `param_id` is required for any exploratory research-label scan.
- If `build_id` or `generated_at` is missing, the scan may run with `metadata_status: partial`, but the ledger entry cannot transition to `scan-passed`, `backtest-approved`, or any promotion state. The lifecycle status remains `needs-tooling` until metadata is supplied or the narrow non-primary-target waiver applies.
- A human may not waive missing `param_id`.
- A human may waive missing `build_id`/`generated_at` only to create a manual backtest handoff when the label is not used as the primary target.

## Missing Metadata Behavior

| Missing Item | Allowed Status | Promotion Allowed |
|---|---|---|
| catalog layer absent entirely | `needs-tooling` | no |
| primary timestamp | `needs-tooling` | no |
| partition columns | `needs-tooling` | no |
| column existence | `invalid-spec` or `needs-tooling` | no |
| data availability for a **bronze** dataset | `needs-more-data` (no fallback — bronze has no substitute) | no |
| data availability / queryability for a **non-bronze** dataset | not a status by itself — apply the Bronze-First `population_action` routing (`populate`/`derive_from_bronze`/`new_dataset_needed`) first; only `needs-more-data`/`needs-tooling` if that routing can't resolve it (e.g. bronze itself lacks the coverage needed to derive the signal) | no, until routed |
| research `param_id` | `invalid-spec` | no |
| research `build_id` or `generated_at` | `needs-tooling` with `metadata_status: partial` | only limited waiver when research label is not primary target |
| timestamp alignment for lead-lag | `needs-tooling` | no |

"Catalog layer present but disagrees with actual storage" (the `BookState`/`BookChange` situation in "Known Gap") is not a missing-metadata case — it's handled by the verification + routing rules above, not this table.

## Inventory Output Contract

Kilimanjaro inventory files must be YAML:

```yaml
schema_version: 1
created_at: "2026-06-18T00:00:00Z"
makalu:
  catalog_path: "/home/lulurun/workspace/Makalu/doc/catalog.yaml"
  query_cli: "python /home/lulurun/workspace/Makalu/src/cli.py query"
datasets:
  - dataset_name: BookState
    view_name: bookstate
    catalog_declared_layer: bronze   # catalog.yaml's claim
    resolved_layer: silver           # actual on-disk path, confirmed via data-check
    query_verified: false            # `DESCRIBE bookstate` fails today — view not registered
    reliability: candidate
    population_action: derive_from_bronze
    primary_timestamp: ts
    partition_columns: [exchange, symbol]
    source_dataset: OrderBook
    path_template: "/panda/makalu-data/silver/dataset=BookState/exchange={exchange}/symbol={symbol}/date={date}/data.parquet"
    columns:
      metadata_source: dataset_definitions_md   # DESCRIBE unavailable since the view isn't registered
      names: ["ts", "best_mid", "vol_imb_k1"]
    availability:
      status: missing
      method: data_check
      exchange: GMO
      symbol: BTC_JPY
      start_date: "2026-01-01"
      end_date: "2026-01-31"
      existing_dates: []
      missing_dates: ["2026-01-01"]
    metadata_warnings:
      - "catalog_declared_layer (bronze) disagrees with resolved_layer (silver); query engine does not register this view"
      - "no populated data for GMO/BTC_JPY; only BINANCE/BTC_USDT is populated"
```
