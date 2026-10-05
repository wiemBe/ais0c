# T-022: Analiz Kataloğu senkronu

## Amaç

QRadar'daki kural ve log source listelerini Analiz Kataloğu'na senkronlamak (D-25). Yeni kayıtlar "tanımsız" olarak eklenir, operatörün doldurduğu alanlara hiç dokunulmaz. Bu görev bittiğinde arayüz (T-029) operatöre doldurması gereken kayıtları gösterebilir.

## Okunacaklar

- `docs/architecture.md` §9 ("Analiz Kataloğu"), §11.2, §11.4
- `docs/impl/data-model.md`: `catalog_rules`, `catalog_log_sources`
- `docs/decisions.md`: D-25, D-33

## İzinli dizinler

- `packages/knowledge/`
- `packages/workflows/`
- `packages/activities/`
- `config/connectors/`
- `config/policies/`

## Kullanılan sözleşmeler

- `ToolIntent`, `ToolResult`, `CatalogMode`

## Kabul kriterleri

1. **Profil:** Gateway'de `qradar-inventory-read` profili tanımlıdır: kural listeleme ve okuma, log source listeleme ve okuma, log source tipi listeleme. Bu profilde Ariel araması yoktur.
2. **Workflow:** `KnowledgeSync` workflow'u Temporal Schedule ile günlük çalışır ve elle de tetiklenebilir. QRadar'ı gateway üzerinden `catalog-sync` sahte ajan çalışmasıyla okur (D-33).
3. **Yeni kayıtlar:** Yeni bir kural veya log source `defined=false` ve `mode=analyze` ile eklenir.
4. **Operatör alanları korunur:** Mevcut kayıtlarda yalnızca QRadar'dan gelen alanlar (ad, tip) güncellenir. `mode`, `min_level`, `context_note`, `has_automated_action`, `attack_techniques` (T-26), `criticality` ve `in_scope` hiçbir zaman ezilmez. Testi vardır.
5. **Sayfalama:** Gateway'in sayfa sınırından uzun listeler eksiksiz okunur. Testi vardır.
6. **İdempotentlik:** Senkron iki kez arka arkaya çalışınca ikincisi hiçbir şeyi değiştirmez.
7. **Lab testi** (`@pytest.mark.lab`): Lab QRadar'daki sistem kuralları ve log source'lar kataloğa düşer.

## Kapsam dışı

- Katalogun API'si ve arayüzü (T-028, T-029)
- Çift kontrol (T-033)
- AI'ın kayıtlar için açıklama taslağı önermesi (sonraki dalga)

## Bağımlılıklar

- T-013

## Notlar

- **`has_automated_action`:** QRadar'ın kural API'si kural yanıtlarını (otomatik aksiyonları) güvenilir biçimde vermeyebilir. Bu alanı operatör doldurur. API'den okunabildiğini lab'da görürsen PR'da raporla, ama alanı otomatik doldurma.
- **Silinen kayıtlar:** QRadar'da artık olmayan kayıtlar katalogdan silinmez. Nasıl işaretleneceğini PR'da öner; veri modelinde bunun için bir alan yok.
