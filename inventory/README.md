# Data Inventory Snapshots

One YAML file per inventory refresh, in the format defined by
`../requirements/makalu-integration-contract.md` ("Inventory Output
Contract").

Naming: `inventory-YYYYMMDDTHHMMSSZ.yaml` (UTC timestamp of the refresh).

Each snapshot records, per dataset: the catalog-declared layer vs. the
resolved (actually verified) layer, whether it's confirmed queryable,
partition coverage, and — when a dataset isn't currently usable for the
needed scope — a `population_action` (`populate` / `derive_from_bronze` /
`new_dataset_needed`) per the Bronze-First Reliability Model. Bronze
(`Trade`, `OrderBook`) is the only dataset ever marked `reliability:
guaranteed`; everything else is `candidate` and re-verified per snapshot,
not assumed from a prior run.

Generate one with `tools/inventory.py`:

```bash
python tools/inventory.py --market GMO:BTC_JPY --market BINANCE:BTC_USDT \
  --start 2026-01-01 --end 2026-01-10
```

v1 limitations (documented in the tool, not silently assumed): datasets
needing partitions beyond exchange/symbol (`MarketPrice`/`VolSpread`'s
`execution_size`, `VolSpread`/`Chart_*`/`TripleBarrierLabel*`'s `param_id`,
`LeadLag`'s `market_a`/`market_b` — detected from the catalog's own
`partition_columns`, not a hand-maintained list) skip the date-availability
check and are marked `status: unknown` with a warning; a hypothesis that
actually needs one of those resolves its specific partition value itself
via the `SELECT DISTINCT` recipe in `../requirements/makalu-integration-contract.md`.
