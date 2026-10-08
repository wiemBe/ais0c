# T-068: Telemetri sınıfları (1/4): katalogda log source'un QRadar durumu ve sınıfları

## Amaç

Skill'ler telemetriyi ürün adıyla değil sınıfla isteyecek: `windows`, `linux`, `email-security`… (T-95). Bu ilk parça kataloğu hazırlar:

- KnowledgeSync her log source'un QRadar'da etkin olup olmadığını (`qradar_enabled`) senkronlar.
- KnowledgeSync tipin varsayılan sınıflarını bir eşleme dosyasından satıra yazar (`default_telemetry_classes`).
- Admin'in atayacağı sınıflar için bir sütun açılır (`telemetry_classes`). Atamanın API'si T-069'dadır.
- Bir CLI, etkin log source'ları tip, sayı ve sınıfla listeler.

QRadar'da devre dışı olan ve QRadar'dan kalkmış log source'ların hiç sınıfı olmaz.

Sıradaki parçalar: T-069 (API), T-070 (skill manifest'i), T-071 (skill bölümünde çözüm).

## Okunacaklar

Yalnızca bunlar:

- `docs/decisions.md`'de T-95 satırı (bütün tasarım burada) ve T-37 (`qradar_enabled`, `missing_since`)
- `docs/impl/data-model.md`: `catalog_log_sources` tablosu (yeni üç sütun yazılı)
- **İzlenecek desen, kurallardaki `qradar_enabled`:**
  - `packages/storage/src/ais0c_storage/models.py:374-376` (`CatalogRuleRow.qradar_enabled`)
  - `packages/storage/src/ais0c_storage/repositories/catalog.py:36-51` (`SyncedRule`, `SyncedLogSource`) ve `:112` (`sync_catalog_rules`), `:341` (`sync_catalog_log_sources`), `:302` (`list_catalog_log_sources`)
  - `packages/knowledge/src/ais0c_knowledge/catalog/inventory.py:98-170` (`_RULES`, `_LOG_SOURCES`, `read_inventory`)
  - `packages/knowledge/src/ais0c_knowledge/catalog/sync.py` (modül docstring'i, `CatalogSyncReport`, `_source_fields`, audit `details`)
- Migration örneği: `packages/storage/src/ais0c_storage/migrations/versions/0006_disabled_status_and_catalog_fields.py` (`qradar_enabled`'ı ekleyen migration)
- CLI örneği: `packages/knowledge/src/ais0c_knowledge/skills/__main__.py`
- YAML: `packages/knowledge/src/ais0c_knowledge/_yaml.py` (`load_yaml_text` tekrarlanan anahtarı reddeder)
- Batch worker: `packages/activities/src/ais0c_activities/runtime.py` (`load_batch_runtime`, `AIS0C_WORKER_ROOT`), `packages/activities/src/ais0c_activities/catalog.py:64-115` (`CatalogSyncActivities`)

## Branch ve worktree

```bash
git worktree add ../ais0c-T-068 -b agent/<araç>/T-068 main
cd ../ais0c-T-068
```

Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz, orada branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `packages/storage/` (enum, migration `0012`, model, repository, testler)
- `packages/knowledge/` (eşleme yükleyicisi, envanter, senkron, CLI, testler)
- `packages/activities/src/ais0c_activities/runtime.py`, `catalog.py` ve testleri
- `config/telemetry/log-source-classes.yaml` (yeni)

`packages/contracts`, `services/api`, `skills/` ve prompt'lar değişmez. API'nin bugünkü testleri değişmeden geçer. Başka bir dosya gerekirse dur ve PR'da yaz.

## Adımlar

Sırayla:

1. **Enum.** `packages/storage/src/ais0c_storage/enums.py`'ye eklenir ve `ais0c_storage/__init__.py`'den dışa aktarılır:

   ```python
   class TelemetryClass(StrEnum):
       """`catalog_log_sources.telemetry_classes` and `default_telemetry_classes` (T-95)."""

       WINDOWS = "windows"
       LINUX = "linux"
       FIREWALL = "firewall"
       IDS = "ids"
       VPN = "vpn"
       WAF = "waf"
       EMAIL_SECURITY = "email-security"
       PROXY = "proxy"
       DNS = "dns"
       EDR = "edr"
       IDENTITY_CLOUD = "identity-cloud"
       DATABASE = "database"
       NETWORK_DEVICE = "network-device"
       SIEM_INTERNAL = "siem-internal"
       OTHER = "other"
   ```

2. **Migration `0012`.** Dosya: `versions/0012_log_source_telemetry.py`, `revision = "0012"`, `down_revision = "0011"`. Docstring 0011'in biçimindedir. `catalog_log_sources`'a şu sütunlar eklenir:
   - `qradar_enabled boolean NOT NULL DEFAULT true`;
   - `default_telemetry_classes text[] NOT NULL DEFAULT '{}'`;
   - `telemetry_classes text[] NULL`.

   Downgrade üçünü düşürür.
3. **Model.** `CatalogLogSourceRow`'a üç alan eklenir. Yorumları `CatalogRuleRow.qradar_enabled`'ın biçimindedir:

   ```python
   qradar_enabled: Mapped[bool] = mapped_column(server_default=text("true"))
   default_telemetry_classes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
   telemetry_classes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
   ```

4. **Repository** (`repositories/catalog.py`):
   - `SyncedLogSource`'a `qradar_enabled: bool = True` ve `default_telemetry_classes: tuple[TelemetryClass, ...] = ()` eklenir.
   - `sync_catalog_log_sources` bu iki alanı yazar: yeni satırda ekler, var olan satırda günceller. `telemetry_classes`'a dokunmaz; o operatör alanıdır.
   - Yeni fonksiyon (dışa aktarılır):

     ```python
     def effective_telemetry_classes(row: CatalogLogSourceRow) -> frozenset[TelemetryClass]:
         """The log source's classes: none when QRadar disabled it or no longer lists it;
         otherwise the admin's `telemetry_classes`, or the type's defaults when those are NULL."""
     ```

   - `list_catalog_log_sources`'a üç filtre eklenir:
     - `qradar_enabled: bool | None = None`;
     - `telemetry_class: TelemetryClass | None = None`: etkin sınıfları içerenler. SQL: `qradar_enabled AND missing_since IS NULL AND coalesce(telemetry_classes, default_telemetry_classes) @> ARRAY[:class]`;
     - `unclassified: bool | None = None`: `true` ise etkin, kalkmamış ve etkin sınıfı boş olanlar.
5. **Eşleme dosyası.** `config/telemetry/log-source-classes.yaml` aynen şudur. Repoda yalnızca genel ürün adı durur; kurulumun özel tipleri buraya yazılmaz.

   ```yaml
   # Default telemetry classes of QRadar log source types (decision T-95). Generic product
   # names only: an installation's own types (Universal DSM, custom types) are classified per
   # log source in the Analysis Catalog. KnowledgeSync writes these defaults to each log source.
   types:
     Microsoft Windows Security Event Log: [windows]
     Linux OS: [linux]
     Linux iptables Firewall: [firewall]
     Fortinet FortiGate Security Gateway: [firewall, vpn]
     Netgate pfSense: [firewall]
     Microsoft Azure Firewall: [firewall]
     F5 Networks BIG-IP AFM: [firewall]
     McAfee Network Security Platform: [ids]
     Snort Open Source IDS: [ids]
     F5 Networks BIG-IP ASM: [waf]
     F5 Networks BIG-IP LTM: [network-device]
     F5 Networks BIG-IP APM: [vpn]
     F5 Networks FirePass: [vpn]
     Cisco VPN 3000 Series Concentrator: [vpn]
     FireEye: [email-security]
     Cisco IronPort: [email-security]
     Proofpoint Enterprise Protection/Enterprise Privacy: [email-security]
     Fortinet FortiMail: [email-security]
     Trend Micro Deep Discovery Email Inspector: [email-security]
     Microsoft Office 365 Message Trace: [email-security]
     Squid Web Proxy: [proxy]
     Microsoft DNS Debug: [dns]
     Microsoft Entra ID: [identity-cloud]
     Custom Rule Engine: [siem-internal]
     SIM Audit: [siem-internal]
     SIM Generic Log DSM: [siem-internal]
     System Notification: [siem-internal]
     Health Metrics: [siem-internal]
     Asset Profiler: [siem-internal]
     Search Results: [siem-internal]
     Anomaly Detection Engine: [siem-internal]
   ```

   Adlar lab QRadar'ının tip listesinden alındı (7.6.0 FP1). `Universal LEEF` bilerek yok: Universal DSM birden çok ürünü taşır, log source başına atanır.
6. **Yükleyici.** Yeni modül `packages/knowledge/src/ais0c_knowledge/catalog/telemetry.py`:

   ```python
   ClassDefaults = Mapping[str, frozenset[TelemetryClass]]
   CLASS_DEFAULTS_FILE: Final = Path("config/telemetry/log-source-classes.yaml")

   class TelemetryConfigError(ValueError): ...

   def load_class_defaults(path: Path) -> ClassDefaults:
       """The type name -> classes mapping. Raises TelemetryConfigError on invalid YAML, a
       duplicate type, an unknown class, an empty class list or a key other than `types`."""
   ```

   YAML `ais0c_knowledge._yaml.load_yaml_text` ile okunur. `catalog/__init__.py`'den dışa aktarılır.
7. **Envanter ve senkron.**
   - `inventory.py`: `_LOG_SOURCES` alanları `"id,name,type_id,enabled"` olur, `expected_evidence` "The ID, name, type ID and enabled state of every log source." olur. `_LogSource`'a `enabled: bool` eklenir. `read_inventory` bunu `SyncedLogSource(..., qradar_enabled=source.enabled)` olarak geçer.
   - `sync.py`: `sync_catalog(session, inventory, *, synced_at, class_defaults: ClassDefaults = {})` (boş varsayılanı `MappingProxyType({})` ile modül sabiti yap). Her log source'a tipinin varsayılanı `dataclasses.replace` ile eklenir; sıralı bir tuple olur.
   - `_source_fields` artık `(name, type_name, qradar_enabled, default_telemetry_classes)` döndürür. Bunlardan biri değişince log source `log_sources_changed`'e girer.
   - Audit `details`'ında `changed` kaydı önceki değerleri de taşır: `previous_qradar_enabled`, `previous_default_telemetry_classes`. `added` kaydı `qradar_enabled` ve `default_telemetry_classes` taşır.
   - `CatalogSyncReport.log_sources_changed` docstring'i "Renamed, given another type, enabled or disabled in QRadar, or given other default classes." olur. Modül docstring'i de buna göre güncellenir.
8. **Batch worker.**
   - `load_batch_runtime`, `AIS0C_WORKER_ROOT` altındaki `config/telemetry/log-source-classes.yaml`'ı yükler. Dosya yoksa ya da geçersizse worker açılışta `RuntimeConfigError` ile durur; mesaj dosya yolunu ve sebebi söyler.
   - `CatalogSyncActivities.__init__`'e `class_defaults: ClassDefaults` eklenir ve `sync_catalog`'a geçilir.
   - `runtime.py`'nin docstring'indeki "the root ... are not used" cümlesi düzeltilir: batch worker kökten yalnızca bu dosyayı okur.
9. **CLI.** `packages/knowledge/src/ais0c_knowledge/catalog/__main__.py`:

   ```text
   uv run python -m ais0c_knowledge.catalog telemetry [--csv PATH]
   ```

   Kataloğu `AIS0C_DATABASE_URL` ile okur (`ais0c_storage.create_engine`). Yalnızca okur, hiçbir şey yazmaz. Çıktı tip başına bir satırdır, etkin log source sayısına göre azalan:

   ```text
   type                                   enabled  excluded  classes
   McAfee Network Security Platform            11         0  ids
   Microsoft Windows Security Event Log         7         0  windows
   Universal LEEF                               1         0  UNCLASSIFIED
   ...
   51 log sources: 50 enabled, 1 disabled, 0 missing; 1 enabled log source unclassified.
   ```

   - `excluded` = devre dışı + kalkmış.
   - `classes` = o tipin etkin log source'larının etkin sınıflarının birleşimi. Boşsa `UNCLASSIFIED` yazılır.
   - `--csv PATH`: her log source bir satır, sütunlar `log_source_id,name,type_name,qradar_enabled,missing,telemetry_classes,default_telemetry_classes,effective_telemetry_classes`. PATH repo içindeyse komut reddeder (çıkış 2): CSV prod verisi taşır.
   - Çıkış kodları: 0 başarı, 2 kullanım ya da yapılandırma hatası.

## Kabul kriterleri ve testler

Her kriterin testi adıyla verilmiştir. Veritabanı testleri paketin mevcut Postgres fixture'larını kullanır.

1. **Migration ve model** (`packages/storage/tests/test_catalog_log_source_telemetry.py`):
   - `test_migration_0012_adds_the_three_columns`: sütunlar, tipleri ve varsayılanları; downgrade düşürür.
   - `test_effective_classes`: atanmış varsa o; yoksa varsayılan; `qradar_enabled=false` boş; `missing_since` dolu boş.
   - `test_list_filters`: `qradar_enabled`, `telemetry_class` ve `unclassified` filtreleri. Örnek: üç satır (Windows etkin, Windows devre dışı, Universal LEEF etkin ve sınıfsız); `telemetry_class=windows` yalnızca ilkini, `unclassified=true` yalnızca üçüncüsünü döndürür.
2. **Eşleme** (`packages/knowledge/tests/test_telemetry_classes.py`):
   - `test_the_repo_file_loads`: repodaki dosya yüklenir; içindeki bütün sınıflar enum'dadır; FortiGate `{firewall, vpn}`'dir.
   - Negatif testler: `test_unknown_class_is_rejected` (`[windwos]`), `test_duplicate_type_is_rejected`, `test_empty_class_list_is_rejected`, `test_other_top_level_key_is_rejected`.
3. **Senkron** (`packages/knowledge/tests/` içinde, mevcut senkron testlerinin yanında):
   - `test_inventory_reads_the_enabled_state`: `list_log_sources` çağrısının `fields`'ı `enabled`'ı içerir; `enabled: false` satırı `qradar_enabled=False` olur.
   - `test_sync_writes_defaults_and_enabled`: yeni log source varsayılan sınıflarıyla eklenir.
   - `test_disabling_in_qradar_is_a_change`: `enabled` true'dan false'a geçince log source `log_sources_changed`'e girer ve audit'te `previous_qradar_enabled: true` bulunur.
   - `test_admin_classes_survive_the_sync`: `telemetry_classes` dolu bir satır senkrondan sonra aynı kalır.
   - `test_second_sync_changes_nothing`: aynı envanterle ikinci senkron hiçbir şey yazmaz (mevcut kural korunur).
4. **Batch worker** (`packages/activities/tests/`):
   - `test_batch_runtime_loads_the_class_defaults`;
   - negatif test `test_batch_runtime_stops_on_an_invalid_class_file`.
5. **CLI** (`packages/knowledge/tests/test_catalog_cli.py`):
   - `test_telemetry_report_lines`: üç satırlık örnekle çıktı yukarıdaki biçimdedir.
   - `test_csv_inside_the_repo_is_refused`: çıkış 2.
   - `test_report_writes_nothing`: komuttan önce ve sonra satırlar aynıdır.
6. **Lab** (lab QRadar, `QRADAR_LAB_URL`; salt okunur):
   - `packages/activities/tests/test_catalog_sync_lab.py`'ye şu iddialar eklenir: lab'ın devre dışı log source'u kataloğa `qradar_enabled=false` ile girer; etkin ve sınıfsız tek tip `Universal LEEF`'tir.
   - Lab testi `~/.config/ais0c/lab.env` ile koşulur: `set -a; . ~/.config/ais0c/lab.env; set +a; uv run pytest packages/activities/tests/test_catalog_sync_lab.py -m lab -s`. Bugün lab'da 51 log source var, 50'si etkin.
   - Koşamazsan PR'da yaz; planner koşar.

## Kontroller

Worktree'de:

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest packages/storage packages/knowledge packages/activities services/api -q
uv run lint-imports
git grep -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

## PR

`.github/pull_request_template.md`'den `../ais0c-prs/PR-T-068.md`'ye yazılır. İçinde şunlar bulunur:

- lab'daki CLI çıktısı (yalnızca tip satırları ve özet; log source adları PR'a girmez);
- sapmalar.

## Kapsam dışı

- API ve çift kontrol (T-069)
- skill manifest'i (T-070) ve prompt'taki skill bölümü (T-071)
- arayüz
- AI'ın sınıf önerisi

## Notlar

- `dev` stack'in mevcut veritabanında migration `0012` worker'ın ya da e2e'nin kendi göçüyle uygulanır; elle SQL yazılmaz.
- QRadar log source tiplerinin kategori alanı yoktur; sınıfı yalnızca tip adı belirler.
- Bu bir migration görevidir. Kullanıcıya bu görev için yüksek effort önerilir (planner.md §4, kural 11).
