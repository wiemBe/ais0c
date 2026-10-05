# T-007: MCP Policy Gateway spike (ContextForge değerlendirmesi)

## Amaç

IBM ContextForge'un, platformun MCP Policy Gateway'i olarak kullanılıp kullanılamayacağını lab'da denemek. Sonuçta ya ContextForge üzerine plugin yazılmasına ya da kendi ince proxy'mizin yazılmasına karar verilir (T-05). Bu görevin çıktısı kod değil, bir karar raporudur.

Süre sınırı: en fazla 3 iş günü.

## Okunacaklar

- `docs/architecture.md` §13 (tamamı), §11.3, §25
- `docs/decisions.md` → T-05

## İzinli dizinler

- `docs/impl/spikes/`

Prototip kodu ayrı bir branch'te kalır ve merge edilmez. Merge edilen tek şey rapordur: `docs/impl/spikes/gateway-contextforge.md`.

## Kullanılan sözleşmeler

- `ToolIntent`, `ToolResult` (yalnızca prototipte)

## Kabul kriterleri

Rapor şu bölümleri içerir:

1. **Kriter tablosu:** architecture §13.4'teki her kabul kriteri için "karşılandı / kısmen / karşılanmadı" sonucu ve kanıtı (konfigürasyon parçası, komut çıktısı).
2. **Profil yetkisi:** T-006 fork'unun `qradar-read` instance'ı arkasında, profil bazlı virtual server'lar kurulmuştur. `qradar-triage-read` token'ıyla, o profilde olmayan bir aracın çağrılamadığı gösterilir.
3. **Plugin kancası:** Çağrı öncesinde çalışan bir plugin, AQL Guard'ı çağırır (T-005 merge edildiyse gerçeği, edilmediyse bir stub). Reddedilen çağrının gerekçesi client'a yapısal olarak döner.
4. **Araç açıklamaları:** Upstream MCP'nin araç açıklamalarının yerine platformun kendi registry'sindeki açıklamaların verilip verilemediği (§13.3).
5. **İşletim:** Compose içindeki kaynak kullanımı (RAM, CPU) ve ek bağımlılıklar (Redis, veritabanı vb.).
6. **Durum:** Lisans, son sürüm ve sürüm olgunluğu.
7. **Karar ve gerekçe:** ContextForge mi, ince proxy mi? T-011 için önerilen yapı (dizinler ve plugin'ler veya proxy modülleri).

## Kapsam dışı

- Prod kalitesinde gateway kodu (T-011)
- Falcon MCP

## Bağımlılıklar

- T-006 (`qradar-read` profiliyle çalışan fork imajı)

## Notlar

- ContextForge 1.0 öncesi bir sürümdür (RC). Bir sonraki sürümde değişebilecek API'lere bağımlılığı raporda ayrıca belirt.
- Kritik bir kriter karşılanmıyorsa (örnek: profil bazlı tam araç listesi veya çağrı öncesi plugin kancası yoksa) karar ince proxy'dir. Bunu erken fark edersen süreyi doldurmadan raporu yaz.
