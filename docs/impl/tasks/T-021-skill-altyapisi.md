# T-021: Skill altyapısı ve ilk üç skill

## Amaç

Skill'lerin biçimini, yükleyicisini ve deterministik router'ını yazmak (T-21). İlk üç skill'i taslak (`draft`) olarak hazırlamak. Skill'i kullanan ajan (Investigation) dalga B'dedir; bu görev yalnızca altyapıyı ve içeriği kurar.

## Okunacaklar

- `docs/architecture.md` §7 ("Skill'ler"), §22 ("Güven katmanları")
- `docs/impl/contracts.md`: `SkillRef`, `PlanStep`
- `docs/impl/repo-structure.md`: `skills/`
- `docs/decisions.md`: T-21

## İzinli dizinler

- `skills/`
- `packages/knowledge/`

## Kullanılan sözleşmeler

- `SkillRef`, `OffenseSnapshot`, `EnrichmentContext`

## Kabul kriterleri

1. **Manifest şeması:** Manifest, architecture §7'deki alanlarla doğrulanır; bilinmeyen alan reddedilir. `tools`, `allowed_tools` veya benzeri yetki veren alanlar ayrıca açık bir hatayla reddedilir: skill yetki vermez.
2. **Yükleyici:**
   - `skill.yaml` ve `instructions.md` üzerinden `sha256` hash'i hesaplar.
   - `approved` bir skill'in hash'i manifest'teki `content_hash` ile uyuşmazsa yükleme hata verir.
   - Prod modunda `draft` skill yüklenmez.
   - Süresi dolmuş skill seçilmez.
   - Aynı kimlik ve sürümden iki skill varsa hata verir.
3. **Injection taraması:** `instructions.md`'de talimat geçersiz kılma kalıpları ("ignore previous" vb.) veya `untrusted_`/`org_context` etiket benzeri ifadeler varsa yükleme hata verir.
4. **Router:** `candidate_skills(...)` deterministik bir fonksiyondur. Offense'in kural ID'lerine, log source tiplerine ve ATT&CK etiketlerine bakar. Yalnızca onaylı, süresi dolmamış ve ilgili ajan rolüne izin veren skill'leri, sabit bir sırayla `SkillRef` listesi olarak döndürür. Her tetikleyici türü ve boş liste durumu için test vardır.
5. **İlk skill'ler:** Şu üç skill `draft` olarak yazılmıştır: `windows-dcsync`, `vpn-new-country`, `password-spraying`. Her birinin:
   - Tetikleyicileri, gerekli telemetrisi ve gerekli kanıtları tanımlıdır.
   - Talimatı İngilizcedir.
   - Eval suite adları listelenmiştir; suite'lerin kendisi T-030'dadır.

## Kapsam dışı

- Skill'in prompt'a eklenmesi ve Investigation ajanı (T-023)
- Orchestrator'ın skill seçimi (T-026)
- Çift kontrollü onay akışı (T-033). O zamana kadar skill'i kullanıcı PR incelemesinde onaylar ve `approved_by` alanını doldurur.

## Bağımlılıklar

- T-013

## Notlar

- Hash hesabında satır sonları ve dosya sırası sabitlenir; aynı içerik her makinede aynı hash'i vermeli.
- Skill manifest modeli `packages/knowledge` içinde düz bir Pydantic modeli olarak yazılır, `ContractModel`'den türetilmez.
- Lab'daki hazır veriler: `s2-dcsync` (4662) ve `s3-vpn-yeni-ulke` senaryoları (T-008). Sentetik log üreticisi `password-spraying` için gereken 4625 event'lerini üretebiliyor, ama ayrı bir spraying senaryosu henüz yok; senaryoyu T-030 ekler.
