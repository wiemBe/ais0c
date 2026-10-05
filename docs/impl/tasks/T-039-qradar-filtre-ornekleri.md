# T-039: QRadar araç açıklamalarında filtre örnekleri

## Amaç

Modelin QRadar REST filtrelerini ilk denemede doğru yazması. 2026-10-05'teki iki lab koşusunda, 8–9 araç çağrısının 3'ü QRadar'dan 422 aldı ("A filter parameter was invalid ... Error Parsing filter"). Model her seferinde ikinci denemede toparladı, ama her hata bir araç çağrısı ve bir model isteği harcıyor.

Hatalı çağrılar:

- `list_source_addresses`: `source_ip in (192.0.2.1, 192.0.2.2)`. IP'ler tırnaksız.
- `list_assets`: `interfaces in (192.0.2.1, 192.0.2.2)`. `interfaces` iç içe bir listedir; bu biçim geçersiz.

`filter` parametresinin açıklaması bütün liste araçlarında aynı genel metin. IP alanları ve iç içe alanlar için örnek yok.

## Okunacaklar

- `docs/architecture.md` §13.3: araç açıklamaları platformun kendi registry'sinden gelir.
- `config/connectors/qradar.yaml`: T-012'nin açıklama düzeltmesi.
- IBM QRadar REST API filtre sözdizimi (API 29.0); lab QRadar 7.6.0 FP1'de doğrulanır.

## İzinli dizinler

- `config/connectors/qradar.yaml`: yalnızca araç ve parametre açıklamaları.
- `services/mcp-gateway/tests/`
- `tests/e2e/`: isteğe bağlı lab testi için.

Bu dizinlerin dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

Yok. Sözleşme ve araç şemaları değişmez; yalnızca `description` metinleri değişir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **IP alanları:** `filter` açıklaması, IP taşıyan her liste aracında doğru örneği o aracın kendi alan adıyla gösterir. Doğru örnek: tırnaklı tek değer ve tırnaklı `in` listesi, ör. `source_ip in ("192.0.2.1","192.0.2.2")`. Bu araçlar: `list_source_addresses` (`source_ip`), `list_local_destination_addresses` (`local_destination_ip`) ve başka profillerdeki benzerleri.
2. **Varlıklar:** `list_assets`'in açıklaması, bir varlığı IP'sine göre bulan ve lab'da çalışan filtreyi gösterir. Biçim `interfaces` → `ip_addresses` → `value` iç içe alanıdır. Biçim lab'da denenerek bulunur ve PR'a yazılır.
3. **Doğrulama:** Açıklamalardaki her örnek filtre, lab QRadar'da salt okunur bir istekle 200 döner. Lab testi `@pytest.mark.lab` ile işaretlenir. Lab yoksa test atlanır.
4. **Statik test:** Açıklamalarda kalan her IP örneği tırnaklıdır. Tırnaksız IP içeren bir örnek, test tarafından reddedilir.
5. **Ölçüm:** T-012'nin lab e2e testi bir kez koşulur. PR'da hatalı (422) araç çağrısı sayısı, öncesindeki 3/8 ve 3/9 ile karşılaştırılır. Bu bir ölçümdür, eşik değildir.

## Kapsam dışı

- Fork'ta veya gateway'de filtre doğrulaması.
- AQL ve AQL Guard.
- Araç şemalarında değişiklik.

## Bağımlılıklar

- Entegrasyon branch'i: `agent/claude-code/integration`. Görev branch'i oradan açılır.

## Notlar

- Lab'da offense açan bir test koşulursa, açık kalan offense'i yalnızca planner (root agent) kapatır. Kodlama ajanı offense kapatmaz.
- Lab kimlik bilgileri `~/.config/ais0c/lab.env` dosyasındadır. Repoya, fixture'a veya PR'a kopyalanmaz; örneklerde RFC 5737 adresleri kullanılır.
