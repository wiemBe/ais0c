# T-012: Lab'da uçtan uca ilk akış

## Amaç

Faz 0'ın çıkış kriterini karşılamak: Lab QRadar'da oluşan bir offense, `OffenseIntake` ve `CaseWorkflow` üzerinden Triage ajanına ulaşır. Ajan gateway üzerinden lab QRadar'ı okur. Kanıt kaydedilir ve `TriageResult` veritabanına yazılır. Triage AQL çalıştırmaz (D-32); AQL kullanan ilk ajan Investigation'dır (Faz 1).

Bu görevden sonra platformun bütün katmanları ilk kez birlikte çalışmış olur.

## Okunacaklar

- `docs/architecture.md` §6, §9, §13, §20, §28 (Faz 0 çıkış kriteri)
- T-009, T-010 ve T-011 görev dosyaları

## İzinli dizinler

- `packages/activities/`
- `packages/workflows/`
- `services/worker/`
- `tests/e2e/`
- `config/agents/`

## Kullanılan sözleşmeler

- `OffenseSnapshot`, `TriageResult`, `ToolIntent`, `ToolResult`, `EvidenceRef`

## Kabul kriterleri

1. **Offense kaynağı:** `OffenseIntake`'in offense kaynağı artık gateway üzerinden lab QRadar'dır. T-010'daki sahte kaynak testlerde kalmaya devam eder.
2. **Triage bağlantısı:** T-010'daki triage stub'ı, `TemporalDurability` capability'siyle çalışan gerçek Triage ajanıyla değiştirilir. Model çağrıları ve araç çağrıları Temporal activity'si olarak görünür.
3. **Uçtan uca test** (`@pytest.mark.lab`):
   - T-008'deki `s2-dcsync` senaryosu lab QRadar'a gönderilir ve bir offense oluşur.
   - Sonunda `cases` tablosunda bu offense için bir `TriageResult` bulunur.
   - `evidence` tablosunda `query_hash`'li en az bir kayıt bulunur.
   - `tool_calls` tablosunda policy kararlarıyla birlikte kayıtlar bulunur.
4. **Dayanıklılık:** Triage çalışırken worker durdurulup yeniden başlatılırsa workflow kaldığı yerden devam eder ve tek bir sonuç üretir. (PR çıktısı)
5. **Sarmalama kontrolü:** Trace veya kaydedilen mesajlar incelendiğinde, modele giden bütün araç sonuçlarının `untrusted_*` sarmalayıcısının içinde olduğu görülür.
6. **Model:** Model çağrıları `soc-fast` alias'ıyla, LiteLLM dev konfigürasyonu üzerinden yapılır. Kodda sağlayıcı adı geçmez.
7. **Belgeleme:** `tests/e2e/README.md`, lab testinin nasıl çalıştırılacağını anlatır: gerekli ortam değişkenleri, compose servisleri, senaryo ve beklenen sonuç.

## Kapsam dışı

- Investigation, Verification ve Reporting (Faz 1)
- QRadar notu ve e-posta (Faz 1)
- Prod ortamı

## Bağımlılıklar

- T-003, T-008, T-009, T-010, T-011

## Notlar

- **Lab'da offense oluşması:** Lab QRadar'da DCSync event'lerinden offense üretecek bir kural yoksa, lab'da bir test kuralı oluşturulur. Bu yapılandırma repoda değil lab'dadır; nasıl yapıldığı PR'da anlatılır.
- **İlk ölçüm:** Bu, gerçek model ile gerçek sorgunun ilk birlikte çalıştığı yerdir. Gecikme ve token kullanımını PR'da raporla; Faz 1 SLA tahmininde kullanılacak.
