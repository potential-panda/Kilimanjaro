# Makalu Integration Contract

## Purpose

This contract defines the exact Makalu metadata and data-discovery behavior Kilimanjaro v1 may rely on. Any metadata not available through the commands below is treated as missing; exploratory scans may run, but promotion to backtest is blocked until the metadata is supplied or waived by a human where allowed.

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

Known catalog/code mismatch to handle explicitly:

- Phase-1 direction treats `BookState` and `BookChange` as bronze because they are near-raw order-book derivatives.
- Current Makalu `src/makalu/cli/common.py` maps `BookState` and `BookChange` under silver for `data-check`.
- Kilimanjaro must preserve the catalog layer as the product-facing layer and record the CLI availability layer separately as `availability_probe_layer` when it differs.

## Query CLI Contract

### List Datasets

Command:

```bash
python /home/lulurun/workspace/Makalu/src/cli.py query --list
```

Expected JSON shape:

```json
{
  "datasets": [
    {
      "dataset_name": "Trade",
      "view_name": "trade",
      "layer": "bronze",
      "description": "Raw trade stream..."
    }
  ]
}
```

Implementation rule:

- `datasets[*].view_name` is required for scanner SQL.
- If `view_name` is absent, derive it as lowercase `dataset_name` only when that matches Makalu querying docs; record `view_name_source: derived`.
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

Expected text shape when all partitions are supplied:

```text
Dataset : Trade
Location: /panda/makalu-data/bronze/dataset=Trade/exchange=GMO/symbol=BTC_JPY
Partition: exchange=GMO / symbol=BTC_JPY
Min date: 2026-01-01
Max date: 2026-01-31
Total   : 30 days on disk
Window  : 2026-01-01 -> 2026-01-31
Missing : 1 days
  2026-01-07
```

Expected text shape when a partition value is omitted:

```text
Available exchange:
  GMO
  BINANCE
```

Rules:

- v1 must parse the exact labels above: `Dataset`, `Location`, `Partition`, `Min date`, `Max date`, `Total`, `Window`, `Missing`, and `Available <key>`.
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
| catalog layer | `needs-tooling` | no |
| primary timestamp | `needs-tooling` | no |
| partition columns | `needs-tooling` | no |
| column existence | `invalid-spec` or `needs-tooling` | no |
| data availability | `needs-more-data` | no |
| research `param_id` | `invalid-spec` | no |
| research `build_id` or `generated_at` | `needs-tooling` with `metadata_status: partial` | only limited waiver when research label is not primary target |
| timestamp alignment for lead-lag | `needs-tooling` | no |

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
    layer: bronze
    canonicality: canonical
    availability_probe_layer: silver
    primary_timestamp: ts
    partition_columns: [exchange, symbol]
    source_dataset: OrderBook
    path_template: "/panda/makalu-data/bronze/dataset=BookState/exchange={exchange}/symbol={symbol}/date={date}/data.parquet"
    columns:
      metadata_source: describe
      names: ["ts", "best_mid", "vol_imb_k1"]
    availability:
      status: available|partial|missing|unknown
      method: data_check|path_template_filesystem|sql_distinct
      exchange: GMO
      symbol: BTC_JPY
      start_date: "2026-01-01"
      end_date: "2026-01-31"
      existing_dates: []
      missing_dates: []
    metadata_warnings: []
```
