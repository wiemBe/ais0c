# T-037: KnowledgeSync'in worker'a bağlanması

## Amaç

T-022'nin katalog senkronunu çalışır hale getirmek. Bunun için:

- `soc-batch` kuyruğunun worker süreci yazılır;
- `knowledge-sync` Schedule'ı worker açılırken kurulur veya güncellenir;
- dev'de envanter token'ının nasıl verileceği belgelenir.

Senkron, workflow ve Schedule tanımı hazırdır (T-022). Bu görev yalnızca onları bir sürece bağlar.

## Okunacaklar

- `docs/architecture.md` §6, §9 ("Analiz Kataloğu")
- `docs/decisions.md`: D-25, T-35, T-37
- `packages/workflows/src/ais0c_workflows/knowledge_sync.py` ve `schedules.py`
- `packages/activities/src/ais0c_activities/runtime.py` (`load_batch_runtime`) ve `catalog.py`
- `services/worker/src/ais0c_worker/` (case worker'ın düzeni, intake Schedule'ı)
- `../ais0c-prs/PR-T-022.md`: "Deviation 3" ve açık sorular

## İzinli dizinler

- `services/worker/`, `services/worker/tests/`
- `deploy/compose/README.md`

Bu dizinlerin dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

- Yok.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Batch worker.** `python -m ais0c_worker batch`, `soc-batch` kuyruğunda `KnowledgeSync` workflow'unu ve `sync_analysis_catalog` activity'sini çalıştırır.
   - Runtime `load_batch_runtime`'tan kurulur.
   - Süreç SIGINT ve SIGTERM ile düzgünce durur.
   - Çağrısız `python -m ais0c_worker` bugünkü case worker'ıdır ve davranışı değişmez. Mevcut e2e testi buna dayanır.
2. **Schedule.** Worker açılırken `knowledge_sync_schedule()` ile Schedule'ı kurar; Schedule varsa yerinde günceller ve durumunu (duraklatılmış mı) korur.
   - `AIS0C_KNOWLEDGE_SYNC_SCHEDULE=off` ise Schedule'a dokunulmaz.
   - Test (Temporal dev server): Schedule'ın spec'i 03:00 Europe/Istanbul ve overlap `SKIP`'tir. İkinci açılış Schedule'ı günceller ve duraklatılmış durumu korur.
3. **Başlangıç kontrolü.** Envanter token'ı yoksa veya gateway başka bir profil sunuyorsa worker açık bir hatayla durur (`RuntimeConfigError`). Test.
4. **Uçtan uca (dev stack).** `AIS0C_DEV_STACK=1` ve lab ayarlarıyla koşan bir test şunları yapar:
   - batch worker'ı başlatır;
   - Schedule'ı elle tetikler (`ScheduleHandle.trigger`);
   - workflow'un sonucunu bekler;
   - katalogdaki kural sayısının sonuçtakiyle aynı olduğunu gösterir.

   Test yalnızca okur; lab'da hiçbir şey açmaz, kapatmaz ve yazmaz. Sonuç PR'a yazılır.
5. **Belge.** `deploy/compose/README.md`, batch worker'ın dev'de nasıl çalıştırılacağını anlatır: ortam değişkenleri ve `secrets/agents/gateway-token-qradar-inventory-read`.

## Kapsam dışı

- Prod compose servisleri (T-031)
- `POST /catalog/sync` (T-028; aynı Schedule'ı tetikler)
- Ayrı bir `batch` kota havuzu (T-35: şimdilik yok)

## Bağımlılıklar

- `main`

## Notlar

- Branch `main`'den açılır.
- Envanter token'ı dev stack'te zaten üretilir (`make_secrets.py`) ve gateway'e verilir.
- Senkron günde bir, gece 03:00'te koşar. Elle tetiklemede Schedule'ın overlap kuralı, süren bir koşunun üstüne ikincisini başlatmaz.
- T-022'nin lab testi (`packages/activities/tests/test_catalog_sync_lab.py`) gateway'i süreç içinde kurar; bu görevin testi worker sürecini dev stack'in gateway'iyle koşturur.
