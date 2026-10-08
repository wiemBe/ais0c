# T-069: Telemetri sınıfları (2/4): API'de log source sınıflarının görülmesi ve atanması

## Amaç

T-068'den sonra katalog her log source için üç bilgi tutuyor: QRadar'da etkin olup olmadığı, tipinin varsayılan sınıfları ve admin'in atadığı sınıflar. Bu görev bunları API'ye açar:

- `GET /catalog/log-sources` bu alanları döndürür ve sınıfa göre filtreler.
- `PUT /catalog/log-sources/{id}` ile admin sınıf atar. Atama çift kontrolden geçer (T-77) ve audit'e yazılır.

Prod'da insan adımı H-9 bu uçla yapılır: sınıfsız tipler (Brightmail, OPSWAT, Universal DSM) atanır (T-95).

## Okunacaklar

Yalnızca bunlar:

- `docs/decisions.md`'de T-95 ve T-77 satırları
- `docs/impl/api.md`: `/catalog/log-sources` satırları (yeni alanlar ve filtreler yazılı)
- **Bugünkü uçlar:**
  - `services/api/src/ais0c_api/routers/catalog.py:84-98` (`log_source(row)`), `:211-280` (GET liste, GET tek, PUT);
  - `services/api/src/ais0c_api/models.py:408-433` (`CatalogLogSource`, `CatalogLogSourceUpdate`).
- **Çift kontrol:** `services/api/src/ais0c_api/changes.py:108-120` (`log_source_values`, `log_source_version`) ve `:333-360` (`_apply_log_source`)
- **Storage:** `packages/storage/src/ais0c_storage/repositories/catalog.py:415` (`update_catalog_log_source`), T-068'in `effective_telemetry_classes` ve `list_catalog_log_sources` filtreleri, `TelemetryClass`
- Testler: `services/api/tests/test_catalog.py`, `test_changes.py`, `api_support.py`

## Branch ve worktree

```bash
git worktree add ../ais0c-T-069 -b agent/<araç>/T-069 main
cd ../ais0c-T-069
```

Ana checkout'ta çalışılmaz, orada branch değiştirilmez. Push yapılmaz.

## İzinli dosyalar

- `services/api/` (router, modeller, `changes.py`, `openapi.json`, testler)
- `packages/storage/src/ais0c_storage/repositories/catalog.py`: yalnızca `update_catalog_log_source`
- `packages/storage/tests/`: yalnızca bu fonksiyonun testleri
- `apps/ui/src/api/schema.d.ts`: yalnızca yeniden üretim. Arayüzün başka dosyasına dokunulmaz; typecheck kırılırsa dur ve PR'da yaz.

## Adımlar

Sırayla:

1. **Storage.** `update_catalog_log_source`'a anahtar kelimeli ve zorunlu bir parametre eklenir: `telemetry_classes: Sequence[TelemetryClass] | None`. Değer sütuna aynen yazılır: `None` → `NULL`, liste → sıralı ve tekrarsız `text[]`. Tek çağıran `changes.py`'dir; orayı da güncelle.
2. **Modeller** (`models.py`):

   ```python
   class CatalogLogSource(ApiModel):
       ...  # bugünkü alanlar
       qradar_enabled: bool
       default_telemetry_classes: list[TelemetryClass]
       telemetry_classes: list[TelemetryClass] | None
       effective_telemetry_classes: list[TelemetryClass]

   class CatalogLogSourceUpdate(ApiModel):
       ...  # bugünkü alanlar
       telemetry_classes: Annotated[list[TelemetryClass], Field(max_length=5)] | None = None
   ```

   Listelerin hepsi sıralıdır. `effective_telemetry_classes` storage'daki `effective_telemetry_classes(row)`'dan gelir.
3. **GET** `/catalog/log-sources`'a üç sorgu parametresi eklenir: `qradar_enabled` (BoolParam), `telemetry_class` (`TelemetryClass | None`) ve `unclassified` (BoolParam). Bunlar storage'ın aynı adlı filtrelerine geçer. Bilinmeyen bir sınıf 422 döner.
4. **PUT** `telemetry_classes`'ı şöyle işler:
   - **Alan gövdede yoksa** atanmış sınıflar değişmez. Handler `body.model_fields_set`'e bakar ve kuyruğa giden `after` değerine satırdaki mevcut `telemetry_classes`'ı koyar. Bu kural önemlidir: bugünkü arayüz bu alanı göndermiyor; o yüzden açıklama düzenlemesi sınıfları silmemeli.
   - `null` tipin varsayılanına döner.
   - Liste atar. Boş liste "bu log source hiçbir sınıfı karşılamaz" demektir.
   - Aynı sınıf iki kez verilirse 422 `catalog.log_source_invalid` döner.
5. **Çift kontrol** (`changes.py`):
   - `log_source_values(row)`'a `"telemetry_classes"` eklenir (sıralı liste ya da `None`). Bu yüzden `log_source_version` da onu kapsar. Bekleyen eski istekler `Stale` olur; bu kabul edilir.
   - `_apply_log_source` değeri `update_catalog_log_source`'a geçer. Audit kaydı yeni değeri taşır.
6. **OpenAPI ve arayüz tipleri:**

   ```bash
   uv run python -m ais0c_api.openapi > services/api/openapi.json
   docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
     -v "$PWD:/repo:z" -w /repo/apps/ui node:22 sh -c \
     "corepack pnpm install --frozen-lockfile && corepack pnpm gen:api && corepack pnpm lint && corepack pnpm typecheck && corepack pnpm test && corepack pnpm build"
   ```

   `schema.d.ts` değişir; başka arayüz dosyası değişmemelidir.

## Kabul kriterleri ve testler

Testler `services/api/tests/test_catalog.py` ve `test_changes.py`'ye eklenir:

1. `test_log_source_shows_its_classes`: varsayılanı `[windows]` olan, sınıfı atanmamış bir satır `effective_telemetry_classes == ["windows"]` döndürür. Aynı satırı `qradar_enabled=false` yap: `effective_telemetry_classes == []`.
2. `test_log_sources_filter_by_class`: `?telemetry_class=windows`, `?unclassified=true` ve `?qradar_enabled=false` beklenen kimlikleri döndürür (T-068'in üç satırlık örneği).
3. `test_unknown_class_is_422`: hem filtrede (`?telemetry_class=windwos`) hem PUT gövdesinde.
4. `test_assigning_classes_waits_for_a_second_admin`: PUT 202 döner, satır değişmez; ikinci admin onaylayınca `telemetry_classes == ["email-security"]` olur ve audit kaydı vardır.
5. `test_put_without_the_field_keeps_the_classes`: atanmış sınıfı olan bir log source'a `telemetry_classes`'sız bir PUT gelir ve onaylanır; sınıflar aynı kalır.
6. `test_null_returns_to_the_default`: `telemetry_classes: null` onaylanınca sütun `NULL` olur, etkin sınıf varsayılandır.
7. `test_operator_cannot_assign`: `operator` rolüyle PUT 403 döner.
8. `test_duplicate_class_is_422`: `["windows", "windows"]` 422 döner.
9. OpenAPI testi (`tests/api/test_openapi_schema.py`) yeni dosyayla geçer. UI kontrolleri container'da geçer.

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest services/api packages/storage tests/api -q
uv run lint-imports
git grep -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

UI kontrolleri: adım 6'daki container komutu.

## PR

`../ais0c-prs/PR-T-069.md`.

## Kapsam dışı

- Arayüzde sınıf alanı ve filtresi (sonraki arayüz görevi; o görev `tr.ts`'e 15 sınıfın Türkçe etiketini ekler)
- KnowledgeSync ve CLI (T-068), skill tarafı (T-070, T-071)

## Bağımlılıklar

- T-068 birleşmiş olmalı (enum, sütunlar, storage filtreleri).
- T-070 ile aynı anda yürüyebilir; dosyaları ayrı.
