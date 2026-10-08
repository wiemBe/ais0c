# T-068: Telemetri sınıfları: log source'ların sınıflandırılması ve skill telemetrisinin kataloğdan çözülmesi

## Amaç

Bugün skill'ler telemetriyi ürün adıyla istiyor. Bu adların bir kısmı QRadar'da yok, bir kısmı bankanın ürünü değil (T-92). Bu görevden sonra skill telemetriyi bir **sınıfla** ister: `windows`, `linux`, `email-security`… Hangi log source'ların o sınıfı karşıladığı kurulumun Analiz Kataloğu'ndan çözülür. Etkin log source'lar tiplerine göre varsayılan bir sınıf alır; admin sınıfsız kalanları atar. QRadar'da devre dışı olan ve QRadar'dan kalkmış log source'lar sayılmaz. Investigation, skill bölümünde her sınıfın bu kurulumdaki tip adlarını ve log source kimliklerini görür; sınıfın etkin log source'u yoksa bunu açıkça görür (T-95).

## Okunacaklar

- `docs/decisions.md`: T-37, T-77, T-92, T-93, **T-95**
- `docs/architecture.md`: Analiz Kataloğu, §7 (skill manifest'i)
- `docs/impl/data-model.md`: `catalog_log_sources`
- `docs/impl/api.md`: `/catalog/log-sources`
- `docs/impl/skill-authoring.md` §2 (sınıf tablosu), §3
- `packages/knowledge/src/ais0c_knowledge/catalog/` (`inventory.py`, `sync.py`), `packages/knowledge/src/ais0c_knowledge/skills/` (manifest, loader)
- `packages/activities/src/ais0c_activities/skills.py`, `packages/agents/src/ais0c_agents/skills.py` (`SkillTelemetry`, `render_skill`)
- `services/api/` (katalog uçları ve çift kontrol, T-033)

## Branch

`agent/<araç>/T-068`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-068 -b agent/<araç>/T-068 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `packages/storage/` (migration `0012`, model, repository)
- `packages/knowledge/` (sınıf listesi, varsayılan eşlemenin okunması, senkron, CLI, manifest alanı, testler)
- `config/telemetry/` (yeni: `log-source-classes.yaml`)
- `packages/activities/src/ais0c_activities/skills.py` ve testleri
- `packages/agents/src/ais0c_agents/skills.py` ve testleri
- `services/api/` (katalog uçları) ve testleri
- `skills/*/1.0.0/skill.yaml`: yalnızca `required_telemetry`. `skills/CATALOG.md`: yalnızca telemetri sütunu. `skills/README.md`: alanın tanımı.

Bu dosyaların dışında hiçbir dosya değiştirilmez. `packages/contracts` değişmez: `CatalogLogSource` aynı kalır, çözüm activity'de storage'dan yapılır. Gerekirse görev durdurulur ve PR'da talep edilir. **Hiçbir skill silinmez.**

## Kullanılan sözleşmeler

`CatalogLogSource`, `EnrichmentContext` (okunur, değişmez).

## Kabul kriterleri

Her madde en az bir testle gösterilir. Güvenlikle ilgili maddelerin negatif testi vardır.

1. **Sınıf listesi.** Sınıflar `ais0c_knowledge`'da sabit bir listedir (T-95): `windows`, `linux`, `firewall`, `vpn`, `waf`, `email-security`, `proxy`, `dns`, `edr`, `identity-cloud`, `database`, `network-device`, `other`. Listede olmayan sınıf her yerde reddedilir: manifest, eşleme dosyası ve API.
2. **Varsayılan eşleme.** `config/telemetry/log-source-classes.yaml` stok QRadar tip adlarını bir ya da birden çok sınıfa bağlar. Repoda yalnızca genel ürün adları durur. En az şu tipler eşlenir:
   - `Microsoft Windows Security Event Log` → `windows`;
   - `Fortinet FortiGate Security Gateway` → `firewall`, `vpn`;
   - `F5 Networks BIG-IP ASM` → `waf`;
   - `FireEye` → `email-security`;
   - `Microsoft Entra ID` → `identity-cloud`.

   Ayrıca lab'daki 51 log source'un tipleri ve yaygın stok tipler eşlenir: Linux OS, Cisco IronPort, Proofpoint, Fortinet FortiMail, Microsoft Exchange Server, Microsoft Office 365 Message Trace, BIG-IP LTM/APM/AFM ve benzerleri. Bir test, dosyanın yüklendiğini ve bütün sınıfların listede olduğunu gösterir. Negatif testler: bilinmeyen sınıf ve aynı tipin iki kez yazılması reddedilir.
3. **Katalog (migration `0012`).** `catalog_log_sources`'a iki sütun eklenir:
   - `qradar_enabled` (bool, varsayılan `true`): KnowledgeSync log source listesini `enabled` alanıyla çeker ve bu sütunu günceller (`list_log_sources` fields: `id,name,type_id,enabled`). Değişiklik, kurallardaki `rules_changed` gibi senkron sonucunda sayılır.
   - `telemetry_classes` (text[], boş olabilir).

   Etkin sınıflar, atanmışsa `telemetry_classes`, yoksa tipin varsayılanıdır. `qradar_enabled=false` ya da `missing_since` dolu olan log source'un etkin sınıfı yoktur.
4. **Telemetri raporu (CLI).** `uv run python -m ais0c_knowledge.catalog telemetry [--csv PATH]` senkronlanmış kataloğu okur. Her tip için şunları yazar: etkin log source sayısı, devre dışı ya da kalkmış olarak dışarıda kalanların sayısı, etkin sınıflar ve sınıfsızsa `unclassified`. CSV'de log source kimlikleri, adları, tipleri ve sınıfları vardır. CSV prod verisi taşır ve repoya girmez; README bunu söyler. Lab'daki 51 log source'la koşulur ve özeti PR'a yazılır. Lab'da sınıfsız tip kalmamalıdır; kalırsa PR'da listelenir.
5. **API** (`/catalog/log-sources`):
   - GET'e `qradar_enabled`, `telemetry_class` ve `unclassified` filtreleri eklenir. Cevapta `telemetry_classes` ve `effective_telemetry_classes` vardır.
   - PUT gövdesine `telemetry_classes` eklenir. Değer sınıf listesinden olmalıdır; `null` tipin varsayılanına döner. Değişiklik çift kontrolden geçer (T-77) ve audit'e yazılır.
   - Negatif testler: bilinmeyen sınıf 422 döner; `operator` rolü 403 alır.
6. **Skill manifest'i.** `required_telemetry[].log_source_type`'ın yerini `telemetry_class` alır (sınıf listesinden). Eski alan reddedilir.
   - 60 skill'in `required_telemetry`'si sınıfa çevrilir. Event satırları ürün adı yerine alanı anlatır (rehber §2). Ürüne özgü alan adı örnek olarak kalabilir.
   - Entra skill'lerindeki "mailbox activity" satırları kalkar, çünkü `email-security` posta kutusu etkinliğini görmez.
   - `skills/CATALOG.md`'nin telemetri sütunu sınıfları gösterir. Katalog testi bunu manifest'lerle karşılaştırır.
7. **Skill bölümünün çözülmesi.** Skill girdisini kuran activity her sınıfı kataloğa göre çözer. `render_skill` her gereksinim için sınıfı, gerekli olup olmadığını, bu kurulumdaki tip adlarını ve tip başına en çok 20 log source kimliğini yazar. Sınıfın etkin log source'u yoksa şu cümleyi yazar: "no enabled log source of this class in this installation; report a data gap".
   - Log source adları prompt'a girmez, çünkü otomatik algılanan adlar logdaki host adını taşır.
   - Tip adı yalnızca güvenli karakterlerden oluşuyorsa yazılır: harf, rakam, boşluk, `.-_()/`, en çok 255 karakter. Aksi halde yalnızca kimlik yazılır.
   - Negatif testler: talimat kalıbı taşıyan özel bir tip adı prompt'a girmez; devre dışı log source'un kimliği listede yer almaz.
8. **Dev'de doğrulama.** Dev stack'in kataloğunda, lab'ın `windows-kerberoasting` ve `web-sql-injection` skill bölümleri doğru tipleri ve kimlikleri gösterir. Bu bir testle ya da PR'daki çıktıyla gösterilir.

## Kapsam dışı

- Arayüzde sınıf alanı (sonraki arayüz görevi; şimdilik CLI ve API)
- AI'ın sınıf önerisi (sonra; mimaride "AI taslak önerir, admin onaylar")
- Router'ın tetikleyicileri (`log_source_types` değişmez)
- Skill talimatlarının içeriği (T-065), aday özeti (T-067)

## Bağımlılıklar

- `main` (T-064'ün ilk partisi, T-92)
- T-065 ile aynı anda yürüyebilir. T-065 `instructions.md`'lere ve katalog notlarına dokunur, bu görev `skill.yaml`'ların `required_telemetry`'sine ve telemetri sütununa. İkisi `packages/knowledge/tests/test_repo_skills.py`'de çakışabilir; ikinci birleşen çözer.

## Notlar

- Prod'da bu işin insan adımı H-9'dur: ilk KnowledgeSync'ten sonra rapor çıkarılır ve sınıfsız tipler atanır (Brightmail, OPSWAT, Universal DSM).
- QRadar'ın log source tiplerinde kategori alanı yoktur; sınıfı tip adından eşleme belirler.
- FireEye DSM'i e-posta dışında başka Trellix ürünlerini de taşıyabilir (NX, HX). Varsayılan `email-security`'dir, log source başına atama bunu düzeltir.
