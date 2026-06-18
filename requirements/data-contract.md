# Kilimanjaro Data Contract

## Ownership Boundaries

Kilimanjaro owns:

- data inventory snapshots derived from Makalu;
- hypothesis specs;
- scan configs and scan reports;
- research ledger entries;
- manual backtest handoff packages;
- links or ingested summaries from Himalaya results.

Kilimanjaro does not own:

- Makalu raw or derived market data;
- Makalu feature pipelines;
- Himalaya simulation/run infrastructure;
- potential-panda-core runtime abstractions or strategy implementations.

## Canonical v1 Artifact Formats

Kilimanjaro v1 must use one canonical machine-readable source format per record type:

| Artifact | Canonical Format | Path Pattern |
|---|---|---|
| inventory snapshot | YAML | `inventory/inventory-YYYYMMDDTHHMMSSZ.yaml` |
| hypothesis spec | YAML | `specs/hyp-YYYYMMDD-NNN.yaml` |
| ledger event stream | JSON Lines | `ledger/events.jsonl` |
| scan report | JSON | `scan-reports/scan-YYYYMMDD-NNN.json` |
| human approval/waiver | JSON Lines event in ledger | `ledger/events.jsonl` |
| handoff source | YAML | `handoffs/hyp-YYYYMMDD-NNN.yaml` |
| human-readable handoff view | Markdown generated from YAML | `handoffs/hyp-YYYYMMDD-NNN.md` |

Markdown is allowed only as generated human-readable output. It is not the source of truth for specs, scan reports, ledger decisions, or approvals.

Atomicity rules:

- New artifact files are written to a temporary file, schema-validated, then atomically renamed.
- `ledger/events.jsonl` is append-only; corrections are new events, not in-place edits.
- Under version control conflict, implementer must preserve both valid events and resolve ordering by `created_at`; duplicate event ids are invalid.

## Makalu Discovery Inputs

Kilimanjaro must discover availability from:

- Makalu catalog: `/home/lulurun/workspace/Makalu/doc/catalog.yaml`
- Makalu query CLI: `/home/lulurun/workspace/Makalu/src/cli.py query`
- documented partition/date probes from Makalu querying docs, where available.

Detailed command contracts, response shapes, and fallback behavior are defined in `makalu-integration-contract.md`.

**Feature-layer column parity is not assumed.** `Feature_Mid` and
`Feature_NearHFT` column sets can differ by exchange. A spec referencing a
feature-layer column must have that exact column confirmed present for the
specific exchange/symbol it targets — confirming only that the *dataset*
has partition coverage for the requested date range is not sufficient. A
spec that passes date-coverage discovery while silently referencing a
column that doesn't exist for that exchange must fail discovery, not fail
later at scan time. The discovery step (or its implementing helper) must
include a column-existence check scoped to exchange/symbol for feature-layer
references, not just a partition/date check.

## Dataset Layers

Kilimanjaro follows Makalu's five-layer structure and framework, but only
depends on one of them. See `makalu-integration-contract.md`'s
"Bronze-First Reliability Model" for the full principle; summary:

| Layer | Reliability | Dataset Examples | Contract Notes |
|---|---|---|---|
| bronze | **Guaranteed.** The only layer Kilimanjaro may assume is present and queryable without per-spec verification. | `Trade`, `OrderBook` | Raw capture. Always sufficient on its own to derive imbalance, microprice, pull/refill, and aggression mechanics if nothing else is usable. |
| silver | Candidate — verify before use. | `BookState`, `BookChange`, `MarketPrice`, `TradeAggression` | `BookState`/`BookChange` are precomputed imbalance/microprice/pull-refill outputs derived from bronze — useful if already populated and actually queryable for the target exchange/symbol, otherwise populate via Makalu's CLI or derive fresh from bronze. (Catalog currently mislabels both as `bronze`; their real on-disk layer is `silver` — confirm actual queryability, don't trust the catalog label. See "Known Gap" in `makalu-integration-contract.md`.) `MarketPrice` provides execution-aware mid/spread by `execution_size`. |
| gold | Candidate — verify before use. | `VolSpread`, `LeadLag`, chart datasets | Window/statistical signals for regimes and cross-market diagnostics. Useful only once confirmed populated for the needed scope. |
| feature | Candidate — verify before use. | `Feature_Mid`, `Feature_NearHFT` | ML-ready tick-level feature tables. Prefer existing columns over deriving new expressions only when the dataset is actually populated for the target exchange/symbol — `Feature_Mid` currently has no populated data anywhere. |
| research | Candidate — verify before use; never canonical. | `TripleBarrierLabel`, `TripleBarrierLabel_tmp`, `BuyLabel_tmp`, `BacktestPnL` | Ephemeral and rebuildable. Must be version-pinned when used. |

A hypothesis is never blocked because a specific silver/gold/feature/research
dataset is unpopulated or unqueryable for the target scope — see the
populate / derive-from-bronze / define-new-dataset decision order in
`makalu-integration-contract.md`.

## Inventory Record

Each inventory entry must include:

```yaml
dataset_name: BookState
catalog_declared_layer: bronze    # catalog.yaml's claim — not trusted as-is
resolved_layer: silver            # verified against actual on-disk path / data-check
query_verified: false             # confirmed via `query --list` / `DESCRIBE`, not assumed from the catalog
reliability: candidate            # bronze datasets are "guaranteed"; everything else is "candidate"
population_action: derive_from_bronze   # none | populate | derive_from_bronze | new_dataset_needed
exchange: GMO
symbol: BTC_JPY
start_date: "2026-01-01"
end_date: "2026-01-31"
available_dates: []
missing_dates:
  - "2026-01-01"
partition_keys:
  exchange: GMO
  symbol: BTC_JPY
  date: "2026-01-01"
columns: []
freshness_checked_at: "2026-06-18T00:00:00Z"
source_catalog_path: "/home/lulurun/workspace/Makalu/doc/catalog.yaml"
query_probe_command: "python src/cli.py query --sql \"DESCRIBE bookstate\""
canonicality: candidate
notes: "Not registered for query today (root cause: catalog says bronze, data lives under silver). GMO/BTC_JPY has zero populated dates; only BINANCE/BTC_USDT is populated. Routed to derive_from_bronze rather than blocking — see makalu-integration-contract.md."
```

`reliability` values:

- `guaranteed`: bronze (`Trade`, `OrderBook`) only;
- `candidate`: everything else — silver, gold, feature, research. Existence in the catalog does not imply availability or query-readiness; each must be verified per spec.

`population_action` values (only set when `query_verified: false` or coverage is insufficient for the spec's window):

- `none`: dataset is already verified queryable and covers the needed scope;
- `populate`: a known Makalu pipeline can produce it for the missing exchange/symbol/date — run it;
- `derive_from_bronze`: compute the needed feature directly from `Trade`/`OrderBook` in the scanner instead of depending on this dataset;
- `new_dataset_needed`: no existing Makalu pipeline produces this signal; define one following Makalu's conventions (see `makalu-integration-contract.md`).

`canonicality` values:

- `canonical`: bronze data — Kilimanjaro's only guaranteed ground truth;
- `candidate`: silver/gold/feature data that exists and is queryable for the target scope, but earns trust per spec, not by default;
- `research-ephemeral`: research-layer outputs that are rebuildable and not stable ground truth.

## Required Metadata By Use Case

### Hypothesis Scan

Required:

- exchange;
- symbol;
- dataset name and layer;
- date range and distinct available trading days;
- timestamp column and timezone assumption;
- feature columns referenced by SQL expression;
- target source and horizon;
- spread/volatility bucket source if regime stability is required;
- research-layer build metadata when target uses research labels.

### Cross-Venue Lead-Lag

Additional required:

- `market_a` and `market_b`;
- timestamp alignment method;
- clock skew check result;
- claimed lag;
- realistic execution latency assumption;
- explicit rejection if lag is smaller than latency plus cost buffer.

### Research-Layer Target

Additional required:

- dataset name;
- `param_id` or equivalent label-parameter id;
- build, regeneration, or pipeline run identifier;
- generation timestamp if available;
- label horizon and barrier assumptions.

If these fields are unavailable, the scan may run only as exploratory and cannot be promoted. `param_id` is mandatory and not waivable. Missing `build_id` or `generated_at` blocks promotion unless a human waiver states that the research label was not the primary target.

## Feature Expression Contract

v1 scanner input must be a SQL expression over whitelisted datasets/columns:

- `BookState`
- `BookChange`
- `MarketPrice`
- `TradeAggression`
- `VolSpread`
- `LeadLag`
- `Feature_Mid`
- `Feature_NearHFT`

Allowed feature expression fields:

```yaml
feature_expression:
  sql: "imbalance_l3 - imbalance_l1"
  source_datasets:
    - layer: bronze
      dataset: BookState
      alias: bs
  required_columns:
    - imbalance_l3
    - imbalance_l1
  timestamp_column: ts
  join_keys:
    - exchange
    - symbol
    - ts
```

Rules:

- No arbitrary Python transforms in v1.
- No future-looking joins or target leakage.
- Join timestamps must use point-in-time-safe rules.
- Any window aggregation must use only data at or before the signal timestamp.
- Feature columns from `Feature_Mid` or `Feature_NearHFT` should be preferred when they already exist.

The exact SQL AST allowlist, function allowlist, join policy, and window-frame rules are defined in `requirements.md` under "Scanner SQL Safety Contract".

## Target Contract

Allowed v1 targets:

- next-N-second mid or microprice return from `MarketPrice` or `BookState`;
- spread-adjusted return using `MarketPrice` spread;
- research-layer label such as `TripleBarrierLabel` or `BuyLabel_tmp`, with pinned version metadata.

Target fields:

```yaml
target:
  type: spread_adjusted_return
  horizon_seconds: 5
  primary_horizon: true
  source_dataset:
    layer: silver
    dataset: MarketPrice
  timestamp_cutoff_rule: "target window starts strictly after signal timestamp"
  cost_adjustment:
    include_half_spread: true
    fee_bps: 0
    slippage_bps: 0
    latency_ms: 100
```

## Ledger Event Contract

`ledger/events.jsonl` is append-only. Each line is one JSON object:

```json
{
  "schema_version": 1,
  "event_id": "evt-20260618-001",
  "hypothesis_id": "hyp-20260618-001",
  "event_type": "status_transition",
  "actor_type": "agent",
  "actor_id": "agent-session-id",
  "created_at": "2026-06-18T00:00:00Z",
  "from_status": "scan-ready",
  "to_status": "scan-failed",
  "artifact_ids": {
    "spec_id": "hyp-20260618-001",
    "inventory_id": "inventory-20260618T000000Z",
    "scan_report_id": "scan-20260618-001"
  },
  "decision_reason": "failed-ic-threshold",
  "details": {}
}
```

Required common fields:

- `schema_version`
- `event_id`
- `hypothesis_id`
- `event_type`
- `actor_type`
- `actor_id`
- `created_at`

Allowed `event_type` values:

- `status_transition`
- `scan_attached`
- `human_approval`
- `waiver`
- `correction`
- `threshold_change`
- `taxonomy_change`

## Scan Report Output

Each scan report must include:

```json
{
  "schema_version": 1,
  "id": "scan-20260618-001",
  "hypothesis_id": "hyp-20260618-001",
  "scanner_version": "v1",
  "config_hash": "",
  "created_at": "2026-06-18T00:00:00Z",
  "data_window": {
    "exchange": "GMO",
    "symbol": "BTC_JPY",
    "start_date": "2026-01-01",
    "end_date": "2026-01-10",
    "distinct_days": 8
  },
  "metrics": {
    "primary_horizon_seconds": 5,
    "ic_method": "spearman",
    "ic_pooled": null,
    "ic_by_day": {},
    "hit_rate": null,
    "bucketed_returns": [],
    "decay_by_horizon": [],
    "raw_sample_count": 0,
    "effective_sample_count": 0,
    "signal_autocorrelation_lag1": null,
    "significance_method": "block_bootstrap_by_day",
    "bootstrap_resamples": 1000,
    "bootstrap_seed": 0,
    "stability_by_regime": {}
  },
  "threshold_verdict": {
    "status": "pass",
    "failed_rules": [],
    "yellow_flags": [],
    "verdict_text": ""
  }
}
```

## Freshness And Cadence

- Inventory should be refreshed before a scan session.
- Scan reports must record the inventory timestamp they depended on.
- Research-layer targets must be revalidated before promotion if their build/regeneration id changed after the scan.
- Ledger audit should run weekly once autonomous scans begin.

## Migration And Backfill

v1 starts with version-controlled YAML, JSON, and JSON Lines records as defined above. To keep migration possible:

- all ids must be stable;
- timestamps must be ISO-8601;
- enums must be explicit;
- scan reports are JSON files and must validate against the scan report schema;
- no decision should depend on prose-only fields.

Natural v2 storage options are SQLite, DuckDB, or Parquet once ledger volume makes file search insufficient. JSON Lines is already the v1 ledger event format, not merely a future option.
