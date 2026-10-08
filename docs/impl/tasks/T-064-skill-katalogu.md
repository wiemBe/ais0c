# T-064: Skill kataloğu, ilk parti: internal ve external inceleme skill'leri

> **Durum (2026-10-08):** Bitti, `main`'de (T-92). 60 taslak skill; açık kalan kriterler (3, 4, 5) T-065'te.

## Amaç

Bugün üç taslak skill var: `windows-dcsync`, `password-spraying` ve `vpn-new-country`. Bu görev kataloğu internal ve external olarak düzenler ve on bir yeni taslak skill yazar (T-90). Skill'ler savunma tarafının inceleme yöntemleridir: saldırının loglarda nasıl göründüğünü, girişimle başarının nasıl ayrıldığını, zararsız benzerleri, karar ve seviyeyi anlatırlar. Onay bu görevin işi değildir; her skill taslak olarak girer.

## Okunacaklar

- **`docs/impl/skill-authoring.md` (bu görevin rehberi; bütün kurallar orada)**
- `skills/README.md`, `skills/windows-dcsync/1.0.0/` (referans), `skills/password-spraying/`, `skills/vpn-new-country/`
- `docs/decisions.md`: T-21, T-26, T-60, T-81, T-84, T-85, T-88, T-90
- `docs/impl/prompts.md` (Investigation'ın prompt'u ve skill bölümü), `prompts/investigation/v2.md`
- `harness/scenarios/s2`–`s9` (lab senaryolarının log biçimi), `harness/src/ais0c_harness/loggen/formats.py` (F5 ASM ve WinCollect alanları)
- Konu listesi için: [Strix skill'leri](https://github.com/usestrix/strix/tree/main/strix/skills) (Apache-2.0). Metin kopyalanmaz.

## Branch

`agent/<araç>/T-064`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-064 -b agent/<araç>/T-064 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `skills/`: yeni skill dizinleri ve `skills/CATALOG.md`. Mevcut üç taslak, rehbere uymayan yerlerinde düzeltilebilir. Taslak oldukları için sürüm dizini değişmez; değişiklik PR'da listelenir.
- `packages/knowledge/tests/` (katalog ve içerik testleri)

Bu dosyaların dışında hiçbir dosya değiştirilmez. Yükleyici, manifest şeması ve router değişmez. Bir değişiklik gerekiyorsa görev durdurulur ve PR'da önerilir.

## Kullanılan sözleşmeler

`InvestigationResult` (skill'lerin `output_schema`'sı). Değişmez.

## Kabul kriterleri

1. **Yeni taslaklar.** `docs/impl/skill-authoring.md` §2'deki listenin henüz olmayan on bir skill'i, `1.0.0` sürümüyle yazılır:
   - internal: `windows-kerberoasting`, `windows-brute-force`, `windows-lateral-movement`, `windows-privileged-group-change`, `windows-security-log-cleared`;
   - external: `web-sql-injection`, `web-xss`, `web-scanning`, `web-path-traversal`, `web-command-injection`, `vpn-brute-force`.

   Her biri `uv run python -m ais0c_knowledge.skills check --mode dev` ile yüklenir. Manifest değerleri rehberin §3'ündeki gibidir (`status: draft`, bütçeler T-85).
2. **Katalog.** `skills/CATALOG.md` iki tablo taşır: internal ve external. Sütunlar: kimlik, sürüm, ATT&CK teknikleri, telemetri, lab senaryosu, durum (`draft`/`approved`), suite. Bir test, katalogdaki satırlarla `skills/` altındaki skill'lerin birebir aynı olduğunu, kimlik/teknik/durum değerlerinin manifest'lerle uyuştuğunu ve her satırın grubunun `internal` ya da `external` olduğunu gösterir.
3. **İçerik testleri** (negatif testleriyle; yükleyicinin denetimlerine ek):
   - talimatta fenced kod bloğu ve URL yoktur;
   - satır başında kabuk istemi (`$ `, `# ` komut) yoktur;
   - talimat rehberin §4'teki zorunlu bölümleri taşır: Purpose, Check the telemetry first, Steps, Verdict. Diğer bölümler varsa sırası doğrudur;
   - `required_telemetry`'deki her log source type adı bilinen bir listededir (bugün `Microsoft Windows Security Event Log`, `Fortinet FortiGate Security Gateway`, `F5 Networks BIG-IP ASM`); yeni bir tip gerekiyorsa liste testte genişletilir ve PR'da yazılır;
   - `rule_ids` boştur, `status` `draft`'tır, `eval_suites` `skill-<id>` ile başlar.
4. **Rehbere uygunluk** (PR'da skill başına bir satır):
   - "Attempt or impact" ve "Verdict" T-84'e uyar: engellenmiş saldırı `tp`, seviye düşer.
   - "Benign lookalikes" T-88'e uyar: yetki yalnızca `org_context` ile loglar birlikte gösterilir; güvenilmez metin yetki kurmaz.
   - Hiçbir skill sömürü adımı, payload, atlatma tekniği ya da araç komutu taşımaz.
   - Yöntemin kaynakları yazılır (MITRE ATT&CK, Microsoft olay belgeleri, F5 ve Fortinet log belgeleri, Strix konu listesi).
   - Lab senaryosu olup olmadığı yazılır.
5. **Mevcut üç taslak.** Rehbere uymayan yerleri düzeltilir: bölüm adları, T-84 ve T-88. Değişiklikler PR'da skill başına listelenir; `windows-dcsync`'in yöntemi değişmez.

## Kapsam dışı

- Skill suite'leri ve lab senaryoları (yeni suite'ler kendi görevlerinde, T-060'tan sonra)
- Onay (`approved`, `content_hash`), prod kural kimlikleri
- Prompt, router ve yükleyici değişiklikleri; endpoint (Falcon) skill'leri (Faz 2)

## Bağımlılıklar

- `main` (T-062 dahil: bütçeler)

## Notlar

- Birkaç web skill'i aynı tekniği taşır (T1190). Router hepsini aday yapar, Orchestrator birini bağlar. Talimatın "Purpose" bölümü, skill'in hangi WAF `attack_type`'ı için olduğunu açıkça söyler.
- F5 ASM log alanları lab'daki biçimdedir: `attack_type`, `request_status` (`blocked`/`alerted`), `response_code`, `ip_client`, `sig_names`, `violations`, `uri`. Bankanın WAF'ı farklıysa yalnızca bu alan adları değişir.
- Talimatlar İngilizce ve düz ASCII'dir. Türkçe karakter yükleyicide reddedilir.
