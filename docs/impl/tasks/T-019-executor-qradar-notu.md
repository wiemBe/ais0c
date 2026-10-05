# T-019: Executor, QRadar offense notu

## Amaç

Analiz kapsamındaki her offense'e yazılacak QRadar notunu üretip göndermek (D-18). Notu LLM değil executor yazar: `NoteContent`'i sabit bir Türkçe şablona yerleştirir, metni temizler, aynı notun iki kez yazılmasını önler ve her yazmadan önce kill switch'i kontrol eder.

## Okunacaklar

- `docs/architecture.md` §9: "Acil bakılması gereken event'ler", "QRadar offense notu", "Ajan SLA'sı"
- `docs/impl/contracts.md`: `NoteContent`, `UrgentEvent`
- `docs/impl/data-model.md`: `notes_written`

## İzinli dizinler

- `packages/executor/` (yalnızca `note` modülü)
- `packages/activities/`

## Kullanılan sözleşmeler

- `NoteContent`, `UrgentEvent`, `DataGap`, `ActionType`

## Kabul kriterleri

1. **Şablon:** Not, architecture §9'daki şablonla üretilir. İlk satır `[AI-SOC] Değerlendirme #<n> · <zaman> · run:<işaret>` biçimindedir. Saatler Europe/Istanbul saatiyle yazılır. Notta en fazla 5 acil event bulunur.
2. **Temizleme:** Nota yalnızca yapısal alanlar ve uzunluğu sınırlı özet/gerekçe alanları girer; kontrol karakterleri ve satır sonları temizlenir. Test: Bir acil event gerekçesine sahte bir `[AI-SOC]` başlık satırı ve yeni satırlar gömülü olsa bile notun yapısı bozulmaz.
3. **Tekrar yazma koruması:** Executor yazmadan önce offense'in notlarını okur. İlk satırdaki `run:` işaretiyle aynı not zaten varsa yazmaz ve `notes_written` kaydını `skipped_duplicate` olarak tutar. Test: Activity, not yazıldıktan sonra ama onaylanmadan önce yeniden denenirse QRadar'da tek not olur.
4. **Kill switch:** Gönderimden hemen önce kontrol edilir. Kapalıysa not yazılmaz ve durum kaydedilir.
5. **Not türleri:**
   - Normal not
   - "AI değerlendirmesi yapılamadı" notu (`no_ai_decision`)
   - Gruba eklenen offense için kısa grup notu

   Her biri için şablon testi vardır.
6. **Activity:** `write_offense_note` activity'si notu gateway üzerinden `qradar-note-write` profiliyle gönderir.
7. **Lab testi:** `@pytest.mark.lab` ile işaretlidir ve yalnızca `QRADAR_LAB_OFFENSE_ID` verildiğinde o offense'e not yazar.

## Kapsam dışı

- E-posta (T-020)
- Notun `CaseWorkflow`'a bağlanması (T-026)

## Bağımlılıklar

- T-017, T-018

## Notlar

- QRadar'ın not uzunluğu sınırını lab'da doğrula ve şablonu bu sınırın altında tut. Gateway'deki sınır (T-018) bununla tutarlı olmalı.
