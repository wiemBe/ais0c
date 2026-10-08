# T-065: Skill kataloğunun incelemesi ve düzeltmeleri

## Amaç

T-064'ün ilk partisi `main`'de: 60 taslak skill (57'si yeni) ve `skills/CATALOG.md` (T-92). Skill'ler yükleniyor ve testler geçiyor, ama planner incelemesinde şu sorunlar çıktı:

- Bazı telemetri adları QRadar'da yok.
- İki skill eksik telemetriyi `fp` gerekçesi yapıyor.
- Eski üç taslak rehberin bölümlerine uymuyor.
- T-064'ün içerik testleri eksik.
- Her skill için yazılması gereken inceleme satırları yazılmadı.

Bu görev telemetri dışındaki sorunları düzeltir ve 60 skill'in hepsini rehbere göre inceler. Telemetri T-068'in işidir (T-95). **Hiçbir skill silinmez.** Entra ve e-posta skill'leri de kalır (kullanıcı kararı, 2026-10-08).

## Okunacaklar

- `docs/impl/skill-authoring.md`: bütün kurallar burada. §2'de telemetri sınıfları, §4'te sorgu kuralları var (2026-10-08'de güncellendi).
- `skills/README.md`, `skills/CATALOG.md`, `skills/windows-dcsync/1.0.0/` (referans)
- `docs/decisions.md`: T-84, T-85, T-88, T-90, T-92, T-93, T-95
- `docs/impl/tasks/T-064-skill-katalogu.md`: kriter 3 ve 4 bu görevde tamamlanır
- `packages/knowledge/tests/test_repo_skills.py`, `skill_helpers.py`

## Branch

`agent/<araç>/T-065`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-065 -b agent/<araç>/T-065 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

**Model ailesi:** Skill'leri yazan aileden farklı bir aile (AGENTS.md, "Reviews"). İlk partiyi GLM yazdıysa Codex ya da Claude Code alır.

## İzinli dizinler

- `skills/`: mevcut skill dizinleri ve `skills/CATALOG.md`. Yeni skill eklenmez, hiçbir skill dizini silinmez. Taslaklar `1.0.0` sürüm dizininde düzeltilir; onaylı sürüm olmadığı için yeni sürüm dizini açılmaz.
- `packages/knowledge/tests/`

Bu dosyaların dışında hiçbir dosya değiştirilmez. Yükleyici, manifest şeması, router ve prompt'lar değişmez. Değişiklik gerekiyorsa görev durdurulur ve PR'da önerilir.

## Kullanılan sözleşmeler

`InvestigationResult` (skill'lerin `output_schema`'sı). Değişmez.

## Kabul kriterleri

Her madde en az bir testle ya da PR'daki inceleme tablosuyla gösterilir.

1. **`required_telemetry`'ye dokunulmaz.** Telemetri T-068'de ürün adından telemetri sınıfına çevrilir (T-95). Bu görev `skill.yaml`'ların `required_telemetry`'sini ve kataloğun telemetri sütununu değiştirmez. Talimatlarda bir ürün adı geçiyorsa ("the WAF's request log" gibi genel anlatım tercih edilir) PR'da listelenir; T-068 onu sınıfla uyumlu hale getirir.
2. **Eksik telemetri `fp` değildir.** `windows-golden-ticket` ve `windows-silver-ticket`'in `fp` maddesi telemetri ya da kapsam boşluğuna dayanıyor. Bu iki skill'de kapsam boşluğu `suspicious` olur ve data gap olarak yazılır (rehber §4, "Missing data is never evidence that the activity is benign"). Bir test, hiçbir skill'in Verdict bölümündeki `fp` maddesinin "gap" ya da "coverage" kelimesi taşımadığını gösterir.
3. **Eski üç taslak** (`windows-dcsync`, `password-spraying`, `vpn-new-country`) rehberin bölümlerini alır: How it looks in the logs, Attempt or impact, Benign lookalikes, Level. Bunlar T-84 ve T-88'e uyar ve güvenilmez metnin yetki kurmadığını söyleyen cümleyi taşır. `windows-dcsync`'in yöntemi (adımlar, kanıtlar, karar mantığı) değişmez; `skill-windows-dcsync` suite'i değişmeden geçer (`fixture`, gerçek model koşusu gerekmez).
4. **İçerik testleri** (T-064 kriter 3), her biri negatif testiyle:
   - talimatta fenced kod bloğu ve URL yoktur;
   - satır başında kabuk istemi (`$ `, `# ` ile başlayan komut, `PS>`) yoktur;
   - talimat 40–130 satırdır;
   - Benign lookalikes bölümü varsa güvenilmez metnin yetki kurmadığını söyleyen bir cümle taşır (kalıp listesi testte durur);
   - Attempt or impact bölümü varsa engellenmiş ya da başarısız girişimi `tp` sayar (T-84).
5. **`fp` maddeleri.** Her `fp` maddesi trafiğin saldırı olmadığını neyin gösterdiğini adlandırır: bir `org_context` olgusu ile uyuşan loglar ya da somut bir log deseni. "a consistent story" gibi belirsiz ifadeler kalkar (örnek: `vpn-brute-force`). Onaylı araç, tarayıcı, test ya da bakım gibi her yetki iddiası `org_context`'e dayanır (T-88).
6. **Katalog** (`skills/CATALOG.md`):
   - "the orchestrator connects the one whose Purpose names the attack shape" cümlesi yanlış: Orchestrator bugün adayın yalnızca kimliğini, gereken kanıtlarını ve bütçesini görür, Purpose'u görmez. Cümle düzeltilir; aday özeti T-067'de gelir.
   - Entra skill'leri için not: banka on-prem AD kullanıyor (2026-10-08), bu telemetri bugün yok. Skill'ler taslak kalır ve öncelikleri en sondadır.
   - Telemetri sütununa dokunulmaz (T-068).
7. **İnceleme tablosu (PR).** 60 skill'in her biri için bir satır: T-84 uyumu, T-88 uyumu, payload, sömürü adımı ya da araç komutu yok, kaynaklar (MITRE ATT&CK, Microsoft, F5, Fortinet ve Trellix belgeleri), lab senaryosu, yapılan değişiklik. Sorunlu bulunan ama bu görevde düzeltilemeyen skill PR'da ayrıca listelenir.

## Kapsam dışı

- Yeni skill, skill silme, onay (`approved`, `content_hash`), prod kural kimlikleri
- Skill suite'leri ve lab senaryoları (T-060 ve sonrası)
- Manifest'e özet alanı ve Orchestrator'ın aday seçimi (T-067)
- Telemetri sınıfları, `required_telemetry` ve katalogdaki telemetri sütunu (T-068)
- Investigation ve Verification prompt'ları (T-066)

## Bağımlılıklar

- `main` (T-064'ün ilk partisi dahil, T-92)

## Notlar

- Talimatlar İngilizce ve düz ASCII'dir.
- Sorgu kuralları (LIMIT, epoch, önce indeksli alanla daraltma) Investigation prompt'unda durur ve skill'lerde tekrarlanmaz (T-93). Yine de "How it looks in the logs" bölümü daraltılacak alanları adlandırır: event kimliği, QID, `attack_type`, `logsourceid`.
- T-068 ile aynı anda yürüyebilir. Bu görev `instructions.md`'lere ve katalog notlarına, T-068 `required_telemetry`'ye ve telemetri sütununa dokunur. İkisi `packages/knowledge/tests/test_repo_skills.py`'de çakışabilir; ikinci birleşen çözer.
