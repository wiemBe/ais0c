# T-050: Aramanın kanıt penceresi ve Investigation'ın araç açıklamaları

## Amaç

1. **Kanıt penceresi.** Gateway, START/STOP'lu bir Ariel aramasının kanıtına (`EvidenceRef.time_start/time_end`) sorgunun kendi penceresini değil ajanın görev penceresini yazıyor (`services/mcp-gateway/src/ais0c_mcp_gateway/pipeline.py`, `_evidence`; yalnızca `LAST` kesin pencereyi kullanıyor). T-55'ten beri ajan sorguları epoch milisaniye START/STOP taşıdığı için kesin pencere bilinir. Kesin pencere kaydedilince T-56'nın Verification penceresi (claim kanıtlarının birleşimi) daralır.
2. **Araç açıklamaları.** Lab koşularında (2026-10-06, offense 30, 33) Investigation olmayan alanlar ve değerler uydurdu: AQL'de `eventname` (yok; `QIDNAME(qid)`), `list_offenses`'ta `offense_source` filtresi (QRadar: "Filtering is unsupported") ve `name` alanı (yok), `qid = 4662` (Windows event ID'si; lab QID'i farklı). T-039'daki gibi araç açıklamalarına doğru örnekler eklenir.

## Okunacaklar

- `docs/decisions.md`: T-27, T-36, T-39, T-55, T-56
- `docs/impl/tasks/T-039-qradar-filtre-ornekleri.md` ve `../ais0c-prs/PR-T-039.md` (aynı türden önceki iş)
- `../ais0c-prs/PR-T-049.md` (açık sorular 1, 2), `../ais0c-prs/PR-T-023.md`

## Branch

`agent/<araç>/T-050`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-050 -b agent/<araç>/T-050 main`). Ana checkout'ta çalışılmaz. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `services/mcp-gateway/src/ais0c_mcp_gateway/pipeline.py`, `services/mcp-gateway/src/ais0c_mcp_gateway/evidence.py` ve gateway'in pencereyi taşıyan diğer modülleri
- `services/mcp-gateway/tests/`
- `config/connectors/qradar.yaml`: yalnızca araç açıklamaları (`description`, örnekler)
- `tests/e2e/`: yalnızca ölçüm için

Bu dosyaların dışında hiçbir dosya değiştirilmez. Fork (`../qradar-mcp`) değişmez.

## Kullanılan sözleşmeler

`EvidenceRef`, `TimeWindow`, `ToolIntent`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Kesin pencere.** Sayısal START/STOP'lu bir `create_ariel_search`'ün kanıtı ve aynı aramanın sonuç çağrılarının kanıtı, sorgunun START ve STOP'unu (UTC) pencere olarak taşır. Metin biçimli START/STOP'ta bugünkü davranış (görev penceresi) kalır, çünkü konsolun saat dilimi bilinmez. `LAST` değişmez.
   - Test: sayısal, metin ve `LAST` için kaydedilen pencere; sonuç çağrısının kanıtı aramanınkini taşır.
2. **Açıklamalar.** `qradar-investigate-read` profilindeki şu araçların açıklamaları doğru örnek taşır:
   - `create_ariel_search`: event adı için `QIDNAME(qid)`; QID'in Windows event ID'si olmadığı ve bir event'in QID'inin önce `QIDNAME`/kategoriyle bulunması gerektiği; `eventname` diye alan olmadığı.
   - `list_offenses`: filtrelenebilen alanlar ve tek tırnaklı bir örnek (T-36 (3)); `offense_source` ile filtrelenemediği; dönen alanların adları.
   - Test: açıklama metinleri `test_descriptions.py`'deki düzene göre doğrulanır; örnek AQL investigate Guard'ından geçer.
3. **Ölçüm (lab, salt okunur).** Planner'ın verdiği kapalı offense'le (`QRADAR_LAB_OFFENSE_ID=30`) `tests/e2e/test_lab_investigation.py` en az iki kez koşulur. PR'a araç çağrısı hataları (upstream ve Guard), sorgu başına satır sayısı ve token yazılır. Hedef: uydurma alan ve filtre hatası olmaması.

## Kapsam dışı

- Investigation'ın prompt'u ve bütçesi (T-030)
- Fork'taki araç kodu

## Bağımlılıklar

- `main` `530c01e` veya sonrası (T-049)

## Notlar

- Dev gateway ana checkout'tan build edilir; lab ölçümü için bu worktree'nin gateway'i ayrı bir container veya süreç olarak çalıştırılabilir (T-048 böyle yaptı: `127.0.0.1:8091`), dev stack'in container'ları yeniden kurulmaz.
- Lab kimlik bilgileri `~/.config/ais0c/lab.env` dosyasındadır; repoya veya PR'a kopyalanmaz. Test offense açmaz, kapatmaz, not yazmaz.
