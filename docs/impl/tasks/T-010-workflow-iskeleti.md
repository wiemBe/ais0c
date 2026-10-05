# T-010: `OffenseIntake` ve `CaseWorkflow` iskeleti

## Amaç

Offense'leri QRadar'dan alıp vakaya dönüştüren iki workflow'u ve bunların deterministik kurallarını yazmak: devreye alma checkpoint'i, tekrar kontrolü, Analiz Kataloğu filtresi, gruplama kararı, öncelik sırası, SLA timer'ı ve sinyaller.

Bu görevde QRadar'a ve modele gerçek çağrı yapılmaz. Offense kaynağı ve triage adımı arayüzlerin arkasındadır ve testlerde sahteleriyle çalışır.

## Okunacaklar

- `docs/architecture.md` §6, §9 ("Analiz Kataloğu", "Offense gruplama ve fırtına koruması", "Ajan SLA'sı"), §20
- `docs/impl/data-model.md` → `offenses_seen`, `offense_groups`, `cases`
- `docs/impl/contracts.md` → `OffenseSnapshot`, `EnrichmentContext`, `TriageResult`
- `docs/decisions.md` → D-25, D-26, T-14

## İzinli dizinler

- `packages/workflows/`
- `packages/activities/`
- `services/worker/`

## Kullanılan sözleşmeler

- `OffenseSnapshot`, `EnrichmentContext`, `TriageResult`, `Level`, `CatalogMode`

## Kabul kriterleri

1. **Devreye alma:** `OffenseIntake` ilk çalıştığında checkpoint'i o anın zamanına ayarlar. Başlangıç zamanı bundan önce olan offense'ler işlenmez (D-26).
2. **Tekrar kontrolü:** Aynı offense iki kez gelirse yalnızca bir kez işlenir.
3. **Katalog filtresi:** Kuralı katalogda `skip` olan offense `skipped` durumuyla kaydedilir ve `CaseWorkflow` başlatılmaz.
4. **Gruplama kararı:** Gruplama, `decide_grouping(...)` adlı saf bir fonksiyondur. Üç sonuç döndürebilir: `full_analysis`, `add_to_group`, `start_group_evaluation`. Her kural için ayrı unit test vardır:
   - Grup anahtarı kural setinin hash'idir; pencere 24 saattir.
   - Bir gruptan saatte en fazla N offense (varsayılan 5) tam analize girer.
   - Kritik varlık içeren, IOC eşleşmesi olan veya katalog tabanı `high`/`critical` olan offense her zaman `full_analysis` alır.
5. **Öncelik sırası:** Bekleyen offense sayısı eşzamanlı vaka sınırını aşarsa, `pre_priority` değeri yüksek olanlar önce başlatılır.
6. **Vaka kimliği:** `CaseWorkflow`'un ID'si `case-<offense_id>`'dir. Aynı ID ile ikinci başlatma reddedilir (ID reuse policy).
7. **Sinyaller:**
   - `offense_updated` sinyali `evaluation_no`'yu artırır ve triage'ı yeniden çalıştırır.
   - `offense_closed` sinyali workflow'u düzgünce sonlandırır.
8. **SLA:** Triage adımı SLA süresi içinde bitmezse vaka `no_ai_decision` durumuna geçer. Test, Temporal test ortamında zaman atlatılarak yazılır.
9. **Determinizm:** Testler Temporal sandbox'ında koşar. Ayrıca testte üretilen bir workflow geçmişi `Replayer` ile yeniden oynatılır ve hata vermez.
10. Workflow kodu ağ, dosya, saat veya rastgele sayı çağrısı yapmaz; bunların hepsi activity'lerdedir.

## Kapsam dışı

- QRadar'dan gerçek offense çekme (T-012, gateway üzerinden)
- Investigation, Verification ve Reporting adımları, QRadar notu, e-posta (Faz 1)
- Grup değerlendirmesinin içeriği; bu görevde yalnızca karar ve durum geçişi var

## Bağımlılıklar

- T-002
- T-004

## Notlar

- **Triage adımı:** Bu görevde triage, bir activity stub'ıdır. T-012'de Pydantic AI'ın `TemporalDurability` capability'si ile gerçek ajana bağlanır. Bu yüzden triage çağrısını tek bir fonksiyonun arkasında tut.
- **Konfigürasyon:** Eşzamanlı vaka sınırı, grup sınırı (N) ve SLA süreleri konfigürasyondan okunur. Varsayılanlar architecture §9'daki değerlerdir.
