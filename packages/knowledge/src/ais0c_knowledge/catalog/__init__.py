"""Analysis Catalog sync: QRadar's rules and log sources into the catalog (architecture §9, D-25).

- `read_inventory(call)` reads every rule, log source and log source type through the
  gateway's `qradar-inventory-read` tools, page by page, or raises `InventoryReadError`.
- `sync_catalog(session, inventory, synced_at=...)` adds new entries undefined, updates only
  the fields that come from QRadar (the enabled state of a rule or log source among them) and
  the default telemetry classes of a log source's type (`load_class_defaults`, T-95), marks the entries
  QRadar no longer lists instead of deleting them (`missing_since`), audits each change and
  returns a `CatalogSyncReport`.

`ais0c_activities` runs both for the `KnowledgeSync` workflow: the reads in a run of the
pseudo agent `catalog-sync` (D-33), the writes only once every list has been read.
"""

from ais0c_knowledge.catalog.inventory import (
    INVENTORY_PAGE_SIZE,
    MAX_NAME_LENGTH,
    MAX_PAGES,
    InventoryReadError,
    ListCall,
    QRadarInventory,
    clean_name,
    read_inventory,
)
from ais0c_knowledge.catalog.sync import (
    LOG_SOURCE_OBJECT,
    LOG_SOURCE_SYNC_ACTION,
    RULE_OBJECT,
    RULE_SYNC_ACTION,
    SYNC_ACTOR,
    CatalogSyncReport,
    sync_catalog,
)
from ais0c_knowledge.catalog.telemetry import (
    CLASS_DEFAULTS_FILE,
    ClassDefaults,
    TelemetryConfigError,
    load_class_defaults,
)

__all__ = [
    "CLASS_DEFAULTS_FILE",
    "INVENTORY_PAGE_SIZE",
    "LOG_SOURCE_OBJECT",
    "LOG_SOURCE_SYNC_ACTION",
    "MAX_NAME_LENGTH",
    "MAX_PAGES",
    "RULE_OBJECT",
    "RULE_SYNC_ACTION",
    "SYNC_ACTOR",
    "CatalogSyncReport",
    "ClassDefaults",
    "InventoryReadError",
    "ListCall",
    "QRadarInventory",
    "TelemetryConfigError",
    "clean_name",
    "load_class_defaults",
    "read_inventory",
    "sync_catalog",
]
