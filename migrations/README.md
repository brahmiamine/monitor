# Inventory schema, version 1

`inventory_store.migration_statements(maria=False|True)` is the single source of
truth for this explicit, additive migration. It creates only `inventory_*` tables
and indexes; the historical Monitor tables are never altered or copied.

The application integration calls `InventoryStore(engine.db, maria=engine.MARIA).migrate()`
at its normal schema initialization point. Repeating it is safe and records version
1 once in `inventory_migrations`. MariaDB's DDL commits implicitly: operators must
back up their database and approve application deployment before applying it in
production. Each statement is idempotent so a partial DDL application can be retried.

## New tables

| Table | Purpose | Bound |
|---|---|---|
| `inventory_state` | Last attempt, success, state, last successful data | One row per registered collector |
| `inventory_config` | Enabled, interval and timeout overrides | One row per registered collector |
| `inventory_snapshots` | Deduplicated technical inventory | 30 days, 100 snapshots per collector by default |
| `inventory_metrics` | Numeric samples independent of inventory changes | 30 days, 5,000 samples per collector by default |
| `inventory_changes` | Bounded structural inventory changes | 30 days, 1,000 changes per collector by default |
| `inventory_relations` | Observed or explicitly inferred resource links | One bounded JSON array per collector |
| `inventory_migrations` | Explicit version applied | One row per schema version |

JSON is limited to 256 KiB, depth 20 and 20,000 nodes. Non-finite numbers,
credential-bearing URLs and sensitive keys are rejected before persistence.
Changes contain at most 100 entries, each with bounded paths and value previews.
Initial baselines and identical or merely reordered inventories do not create
change events. Dynamic PSI/cgroup values should be stored with `snapshot=False`
and numeric `metrics` so that changing load does not flood inventory history.

Retention executes at most once per minute through `record()`; operators may call
`prune()` independently. Caps are per collector and enforced during pruning, not
on every sample. Query limits are independently bounded. A comparison fails
explicitly if a snapshot has expired, and cannot compare different collectors.

## Explicit rollback

Stop the application version that uses these tables first, and obtain operator
approval for production data deletion. Then call `store.rollback()` using the
same configured database adapter. This drops only the seven `inventory_*` tables,
including their new history and configuration. Historical Monitor data is not
changed. Rollback is idempotent; applying `migrate()` afterwards creates a fresh
inventory schema. Restoring the inventory history itself requires the database
backup taken before deployment.

Tests in `tests/test_inventory_store.py` exercise both directions against a
temporary SQLite file and verify preservation of a pre-existing table. The main
integration additionally validates MariaDB in an isolated temporary server; this
document does not claim that checking generated SQL alone validates that engine.
