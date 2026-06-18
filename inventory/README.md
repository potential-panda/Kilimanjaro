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
