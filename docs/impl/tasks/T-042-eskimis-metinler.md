# T-042: T-040 ve T-041'den kalan eskimiş metinler

## Amaç

T-040 ve T-041'in izinli dizinleri dışında kaldığı için güncellenmeyen metinleri yeni davranışa uydurmak. Davranış değişmez; yalnızca yorum, doküman ve test sabitleri değişir.

1. **Fork README ve NOTICE.** README'nin 35. satırı "Upstream'in Python kodu değiştirilmedi" diyor. T-040 üç upstream dosyasını değiştirdi: `tools/offense/add_offense_note.py`, `tools/reference_data/get_reference_table.py`, `tools/reference_data/get_reference_map.py`. NOTICE fork'un değişikliklerini listeliyor ama bunları içermiyor. README'deki upstream senkron listesi ("Fork'un bağlandığı upstream dosyaları") da bu üç dosyayı saymıyor; upstream bunları değiştirirse birleştirme elle yapılmalı.
2. **Not politikasının yorumu.** `config/policies/qradar.yaml`, `text_arguments` `max_length`'inin "karakter" saydığını ve 2000'in QRadar not penceresinin sınırı olduğunu, API'nin sınır belgelemediğini, T-019'un lab'da kontrol edeceğini yazıyor. T-040'tan beri gateway UTF-16 birimi sayıyor ve sınır lab'da ölçüldü: 2000 UTF-16 birimi (T-019, 2026-10-04).
3. **Test sabitleri.** `packages/executor/tests/test_note_writer.py` ve `packages/activities/tests/test_note_activity.py`, gateway'in eski ret metnini ("... longer than 2000 characters") sahte cevap olarak kullanıyor. Yeni metin: `note_text is longer than 2000 UTF-16 code units`.
4. **KnowledgeSync test fixture'ı.** `packages/workflows/tests/test_knowledge_sync_workflow.py`'nin sabit `COUNTS` sözlüğü T-041'de değişen anahtarı (`rules_renamed`) taşıyor. Yeni anahtarlar `CatalogSyncReport.counts()`'tan alınır (`rules_changed`, `rules_marked_missing`, `rules_returned`, `log_sources_marked_missing`, `log_sources_returned`).

## Okunacaklar

- `../ais0c-prs/PR-T-040.md` ve `PR-T-040-fork.md`: sapmalar ve açık sorular.
- `../ais0c-prs/PR-T-041.md`: sapma 2, açık soru 4.
- `docs/decisions.md`: T-34, T-37.

## Branch'ler

- Fork: `../qradar-mcp`'de `agent/<tool>/T-042`, fork'un entegrasyon branch'inden (`agent/claude-code/integration`, `7dcf3ce`) açılır. Push yapılmaz.
- ais0c: `agent/<tool>/T-042`, entegrasyon branch'inden (`agent/claude-code/integration`) ayrı bir worktree'de açılır.

## İzinli dizinler

Fork: `README.md`, `NOTICE`.

ais0c:

- `config/policies/qradar.yaml`: yalnızca yorumlar
- `packages/executor/tests/test_note_writer.py`
- `packages/activities/tests/test_note_activity.py`
- `packages/workflows/tests/test_knowledge_sync_workflow.py`

Bu dosyaların dışında hiçbir dosya değiştirilmez. Fork'ta Python kodu değişmez. Fork commit'i `server_version`'ı değiştirmez; README ve NOTICE imaja girmez, sabitlenen commit `7dcf3ce` kalır.

## Kullanılan sözleşmeler

Yok.

## Kabul kriterleri

1. Fork README'si değiştirilen üç upstream dosyasını adlarıyla sayar ve T-040'a bağlar; upstream senkron listesi onları içerir. NOTICE değişiklikleri listeler. Fork'un testleri ve snapshot kontrolü (`python -m qradar_mcp.fork.schema_export --check`) geçer.
2. `config/policies/qradar.yaml`'ın yorumu birimi (UTF-16 kod birimi) ve sınırın kaynağını (T-019'un lab ölçümü) yazar. YAML'ın yüklenen verisi değişmez: gateway'in registry testleri geçer.
3. İki test dosyasındaki sahte ret metni gateway'in güncel metnidir. Testler geçer.
4. KnowledgeSync workflow testi `CatalogSyncReport.counts()`'un güncel anahtarlarını kullanır; mümkünse sözlüğü elle yazmak yerine raporun kendisinden üretir. Test geçer.
5. Tam suite, ruff, pyright ve lint-imports geçer.

## Kapsam dışı

- Davranış değişikliği.
- Referans map filtresinin geri eklenmesi (T-34: hunt dalgası, lab'da bir map gerekir).
- Lab koşusu: gerekmez.

## Bağımlılıklar

- Entegrasyon branch'i (`agent/claude-code/integration`, uç `4113d81` veya sonrası).
- Fork'un entegrasyon branch'i (`7dcf3ce`).

## Notlar

- Yeni worktree'de `docs/impl` yoktur. Testler için ana checkout'tan `docs/impl/*.md` dosyalarına tek tek symlink açılır ve commit'ten önce silinir.
