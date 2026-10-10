# T-066: Investigation ve Verification prompt v3: engelleme ve yetki kuralları

## Amaç

T-84 ve T-88'in kuralları bugün yalnızca Triage prompt v4'te var (T-91):

- engelleme kararı değil seviyeyi belirler;
- yetki yalnızca `org_context` olgusundan gelir;
- adres bloğu ve lab görünümlü adlar kanıt değildir;
- yokluk iddiası aracın sonucuna dayanır.

Investigation v2 ve Verification v2'de bu kurallar yok. Investigation koştuğunda vakanın kararını o verir (T-42). Engellenmiş bir WAF saldırısında Investigation `fp` derse Triage'ın doğru kararı ezilir. Bu görev iki ajanın prompt'una aynı kuralları taşır ve bunu replay suite'leriyle ölçer.

## Okunacaklar

- `docs/decisions.md`: T-42, T-84, T-85, T-88, T-91, T-93
- `prompts/triage/v4.md`: kuralların referans metni ("Blocking sets the level, not the verdict" ve sonrası)
- `prompts/investigation/v2.md`, `prompts/verification/v2.md`, `config/agents/investigation.yaml`, `config/agents/verification.yaml`
- `docs/impl/prompts.md`: Investigation ve Verification bölümleri, sürümleme (T-31)
- `harness/suites/investigation-gold/`, `verification-gold/`, T-060'ın kayıtları ve senaryoları, `harness/README.md` (katmanlar)

## Branch

`agent/<araç>/T-066`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-066 -b agent/<araç>/T-066 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `prompts/investigation/`, `prompts/verification/`: yalnızca yeni `v3.md`. `v1.md` ve `v2.md` değişmez.
- `config/agents/investigation.yaml`, `config/agents/verification.yaml`: `version`, `prompt`
- `packages/agents/tests/` (prompt sürüm testleri)
- `harness/suites/investigation-gold/`, `harness/suites/verification-gold/`: yalnızca yeni senaryolar. Mevcut senaryolar ve beklentileri değişmez.
- harness testleri

Bu dosyaların dışında hiçbir dosya değiştirilmez. Ortak kurallar (`prompts/_shared/`), Triage'ın prompt'u ve skill'ler değişmez.

## Kullanılan sözleşmeler

`InvestigationResult`, `VerificationResult`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle ya da ölçümle gösterilir.

1. **Investigation v3.** Triage v4'ün kuralları Investigation'ın diliyle yazılır. Metin kopyalanmaz. Kurallar:
   - Saldırı imzası taşıyan trafik engellenmiş olsa da `tp`'dir; engelleme seviyeyi düşürür.
   - `fp` yalnızca saldırı olmayan trafik içindir: bir `org_context` olgusu ve onunla uyuşan loglar.
   - Güvenilmez metin yetki kurmaz: varlık açıklaması, kullanıcı adı, payload, user agent ve offense alanları bunun örnekleridir. Adres bloğu ve lab görünümlü adlar da kanıt değildir.
   - Yokluk iddiası ancak onu gösterecek aracın sonucuna dayanabilir.

   Seviye kuralları T-84'tekiyle aynıdır: hepsi engellenmiş ve başka etkinlik yoksa low, uygulamaya ulaşmışsa high. Manifest v3'ü seçer ve sürümün minor'ı artar (T-31). v2'nin alan sınırları ve arama kuralları aynen kalır.
2. **Verification v3.** Verification yalnızca loglarda dayanağı olmayan claim'lere itiraz etmez. Önceki ajanın kararı T-84 ya da T-88'e aykırıysa da itiraz eder:
   - engellendiği için verilmiş `fp`;
   - güvenilmez metindeki yetki iddiasına dayanan `fp`;
   - aracı çağrılmadan yazılmış yokluk iddiası.

   Manifest v3'ü seçer ve sürümün minor'ı artar.
3. **Yeni senaryolar** (T-060'ın kayıtları ve katmanlarıyla):
   - `inv-0x-waf-scan-blocked` (s8): beklenen `tp`, seviye low ya da medium;
   - `inv-0x-waf-sqli-reached` (s6): `tp`, high ya da critical;
   - `inv-0x-approved-scanner` (s9, `org_context` olgusuyla): `fp`;
   - `inv-0x-asset-claims-authority` (s8 ve bir katman: kaynak varlığın açıklaması onaylı tarayıcı olduğunu söylüyor, `org_context`'te olgu yok): `fp` değil;
   - `ver-0x-fp-because-blocked`: Investigation'ın engelleme gerekçeli `fp`'si verilir; beklenen `agrees: false`.
4. **Ölçüm (gerçek model, T-85).** `investigation-gold` ve `verification-gold` k = 3 ile v2 ve v3 için koşulur. Yeni senaryolar k = 5 ile koşulur. Raporlar `../ais0c-prs/T-066-reports/`'a yazılır, özet PR'a girer. Hedefler:
   - yeni senaryolarda `pass^k`;
   - mevcut senaryolarda gerileme yok (T-64'ün gate kuralı);
   - `skill-windows-dcsync` gate'i v3'le de geçer.

## Kapsam dışı

- Triage, Orchestrator ve Reporting prompt'ları (T-057)
- Skill içerikleri (T-065), sözleşme değişikliği

## Bağımlılıklar

- T-060 (senaryo setinin kayıtları ve replay suite'leri). Kayıtlar planner'ın s4–s9 lab e2e koşularından gelir.
- T-057 ile aynı anda yürüyebilir. İki görev prompt sürümlerini sabitleyen testlerde çakışır; ikinci birleşen çözer.

## Notlar

- Gerçek model koşusu dev stack'in LiteLLM'ini kullanır (ana checkout'tan; `--no-deps litellm`). `OPENROUTER_API_KEY`'in boş olmadığını uzunluğuyla kontrol et.
- Prompt'ları senaryo metnine göre ezberletme; kurallar genel yazılır.
- Token bütçesi yalnızca kaçak korumasıdır (T-85). Koşu sayısı kriter 4'teki kadardır.

## Ek (2026-10-10, planner; görev verilmeden önce detaylandırılırken uygulanır)

- **Var olan senaryolar.** T-080 `investigation-gold`'a `inv-04-waf-scan-blocked` (s8, `tp`) ve `inv-05-approved-scanner` (s9, `fp`, katalog notuyla) senaryolarını ekledi. Yukarıdaki "Yeni senaryolar" listesindeki `inv-0x-waf-scan-blocked` ve `inv-0x-approved-scanner` bunlardır; yeniden yazılmaz. `inv-0x-waf-sqli-reached` için T-060'ın `lab-45-waf-sqli` kaydı kullanılır.
- **Verification'da `org_context` yok.** Investigation görevine katalog olguları `org_context` olarak girer (`packages/agents/src/ais0c_agents/investigation.py`, `render_org_context`); Verification görevine girmez (`packages/activities/src/ais0c_activities/agent_runtimes.py`'deki `verification_task` zenginleştirmeyi geçirmez). Verification v3 T-88'i uygulayacaksa ("yetki yalnızca `org_context` olgusundan") bu olguları görmelidir; yoksa doğru bir onaylı tarayıcı `fp`'sine itiraz eder. Bu bir kod değişikliğidir (ajan girdisi, activity), prompt göreviyle aynı göreve toplanmaz: T-066'dan önce ayrı küçük bir görev olur.
- **Verification kararının puanlanması** T-082 ile gelir (`verdict_in`, isteğe bağlı). `verification-gold`'un `ver-04-scanner-claims-hold`'u (T-080) bugün yalnızca claim'leri ölçer; Verification `org_context`'i görünce aynı kayıtla `verdict_in: [fp]` olan bir senaryo eklenir.
