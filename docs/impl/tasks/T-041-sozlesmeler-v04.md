# T-041: Sözleşmeler v0.4 ve veri modeli değişiklikleri

## Amaç

Dalga A PR'larında istenen ve onaylanan sözleşme ve veri modeli değişikliklerini koda almak (karar T-37). Dokümanlar zaten güncel; bu görev kodu onlara uydurur.

1. **`disabled` durumu.** Kill switch kapalıyken (shadow modu) yazılmayan not bugün `failed` ve `writes_disabled: ` önekli bir hatayla kaydediliyor; e-posta ise hiç kaydedilmiyor. Shadow'da her analiz edilen offense'in bir `failed` satırı olur ve T-032'nin "not/e-posta hataları arttı" alarmı bunu ayıklamak zorunda kalır.
2. **`missing_since`.** QRadar'ın artık listelemediği kural ve log source'lar katalogda işaretsiz kalıyor.
3. **`qradar_enabled`.** Lab'da 134 kuralın 38'i QRadar'da kapalı; katalogda hepsi "tanımsız" görünüyor ve süzülemiyor.
4. **`RunId` deseni ve `CriticalAssetHit.label` sınırı.** Geçersiz bir run ID'si bugün ancak gateway'de yakalanıyor; etiketin uzunluk sınırı yok.
5. **`update_offense_seen(first_seen_at=...)`.** T-014'te intake, yeniden kabul edilen offense'in `first_seen_at`'ini storage'ı atlayan kendi `UPDATE`'iyle sıfırlıyor (T-30).

## Okunacaklar

- `docs/decisions.md`: T-37, T-30 (5), T-23, T-35.
- `docs/impl/contracts.md`: `ToolIntent` (`RunId`), `EnrichmentContext` (`critical_asset_hits`).
- `docs/impl/data-model.md`: `notes_written`, `notifications`, `catalog_rules`, `catalog_log_sources`.
- `docs/impl/api.md`: katalog filtreleri (API'nin kendisi T-028'dedir).
- `../ais0c-prs/PR-T-017.md`, `PR-T-019.md`, `PR-T-020.md`, `PR-T-022.md`: istekler ve gerekçeleri.

## İzinli dizinler

- `packages/contracts/`
- `packages/storage/`
- `packages/executor/`: not ve e-postanın `disabled` kaydı
- `packages/knowledge/`: katalog senkronu
- `packages/activities/`: intake'in `first_seen_at`'i ve testler
- `services/mcp-gateway/`: `RunId` deseninin sözleşmeye geçmesiyle değişen davranış ve testler
- `config/connectors/qradar.yaml`: yalnızca senkronun `enabled` alanını okuması gerekiyorsa açıklama
- Bu paketlerin testlerinin kullandığı yardımcı dosyalar (`services/worker/tests/` dahil), yalnızca yeni sözleşmeye uymak için

Bu dizinlerin dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

`ToolIntent`, `EnrichmentContext`. `packages/contracts` bu görevde değişir (insan onayı: T-37). Sürüm `0.4.0` olur, JSON şemaları yeniden üretilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **`RunId`:** `ToolIntent.run_id` `[A-Za-z0-9][A-Za-z0-9._:-]{0,199}` desenine uymayan değeri modeli kurarken reddeder (boş, 201 karakter, boşluk, NUL, `/`, ilk karakter `.` gibi). Platformun ürettiği bütün run ID biçimleri (`case-27-triage-1`, `case-27-triage-1-retry`, intake ve executor sahte run'ları, hunt ID'leri) kabul edilir. Gateway'de bu biçimdeki bir `run_id` artık `gateway.invalid_intent` alır; kayıtlı olmayan geçerli bir ID yine `gateway.unknown_run`'dır. Gateway'in kendi regex'i sözleşmeye bırakılır veya aynı kalır; iki kontrol birbirinden farklı olamaz (test).
2. **`label`:** `CriticalAssetHit.label` 300 karakterden uzun olamaz.
3. **Migration `0006`:**
   - `notes_written.status` ve `notifications.status` `disabled` değerini alır; storage'ın `NoteStatus` ve `NotificationStatus` enum'ları da.
   - `notifications.error text NULL`.
   - `catalog_rules.qradar_enabled bool NOT NULL DEFAULT true`, `catalog_rules.missing_since` ve `catalog_log_sources.missing_since` (`timestamptz NULL`).
   - Veri taşıma: `status = 'failed' AND error LIKE 'writes_disabled:%'` olan `notes_written` satırları `disabled` olur.
   - Downgrade: `disabled` satırlar `failed` ve `writes_disabled: ` önekli hata olur; sütunlar düşer. Upgrade ve downgrade veri içeren bir veritabanında test edilir; head `0006`'dır.
4. **Not:** Kill switch kapalıyken not denemesi `disabled` kaydedilir; hata metni yazılmaz. Daha önce `disabled` kaydedilmiş bir not, yazma açılınca yeniden denenir ve yazılır.
5. **E-posta:** Kill switch kapalıyken deneme, alıcıları, konusu ve seviyesiyle `disabled` kaydedilir. `failed` ve `rejected` kayıtlar nedeni `error`'a yazar. Yalnızca `sent` kayıt gönderilmiş sayılır: `alert_needed` `disabled` kaydı saymaz ve `disabled` anahtar yazma açılınca yeniden denenir. Alan adı kontrolü yine kill switch'ten önce koşar.
6. **Katalog senkronu:**
   - Her kuralın `qradar_enabled`'ı QRadar'ın `enabled` alanından gelir; değişiklik audit kaydı alır (`catalog.rule.sync`).
   - Eksiksiz bir okumada QRadar'ın listelemediği kural ve log source'ların `missing_since`'i, boşsa, senkron zamanı olur; doluysa değişmez. Kayıt geri gelince `missing_since` boşalır. Okuma eksikse (`InventoryUnreadable`) hiçbir işaret değişmez.
   - İkinci bir senkron hiçbir şey değiştirmez. Operatör alanları (`attack_techniques` dahil) korunur.
   - `to_catalog_rule` ve `to_catalog_log_source` bu alanları `CatalogContext`'e taşımaz (sözleşmede yoklar); zenginleştirme `missing_since`'i dolu kaydı kullanmaya devam eder.
7. **`first_seen_at`:** `update_offense_seen` `first_seen_at` parametresi alır. Intake'in kendi `UPDATE`'i kalkar; T-014'ün yeniden kabul testleri geçmeye devam eder.

## Kapsam dışı

- Katalog API'si ve arayüzü (T-028, T-029): yeni filtreler `api.md`'de yazılı, kodları orada.
- T-032'nin hata alarmları.
- `NoteContent`'te değişiklik (T-33: alınmadı).
- `MaintenanceWindow` ve `KnowledgeItem`'ın sözleşmeye taşınması (veri geldiğinde, Faz 3).

## Bağımlılıklar

- Entegrasyon branch'i (`agent/claude-code/integration`, uç `d5b5108` veya sonrası).
- T-040 ile paralel koşabilir: ikisi de `services/mcp-gateway/`'e dokunur ama farklı dosyalara (T-040 `text_rules.py`, bu görev intent kontrolü). Hangisi ikinci birleşirse çakışmayı o çözer.

## Notlar

- Sözleşme değişikliği onaylıdır (T-37); PR'a `contract-change` etiketi yazılır.
- Migration numarası entegrasyon branch'inde `0005`'ten sonra gelir. Başka bir görev araya migration koyarsa, ikinci birleşen numarasını kaydırır.
- Yeni worktree'de `docs/impl` yoktur. Testler için ana checkout'tan `docs/impl/*.md` dosyalarına tek tek symlink açılır ve commit'ten önce silinir.
- İkinci bir `packages/<pkg>/tests/__init__.py` eklenmez.
- `catalog_rules.qradar_enabled` analiz kararını etkilemez: kapalı bir kuraldan offense gelmez, gelirse yine analiz edilir.
