# T-048: Ajanın son cevabı, Investigation bütçesi ve Ariel zaman penceresi

## Amaç

Investigation lab'da bir sonuç üretsin (T-52, T-53) ve T-047 incelemesinden kalan küçük düzeltmeler yapılsın (T-54). T-023'ün altı lab koşusunun hepsi token bütçesine takıldı ve sonuçsuz bitti (`../ais0c-prs/PR-T-023.md`, "Lab run").

1. **Son cevap.** Pydantic AI token sınırını cevap eklendikten sonra denetler; sınırı aşan son cevap, model sonucu vermiş olsa bile kaybolur. Ortak runner, bütçe bitmeden araçları geri çekip modeli sonuca zorlar. Bu, araçlı her ajan için geçerlidir (Triage, Investigation, Verification).
2. **Bütçe ve araç listesi.** Her istek araç şemaları ve talimatla yaklaşık 10.000 token taşır. Investigation'ın ve taslak skill'lerin token bütçesi artar; katalogda zaten bulunan dört listeleme aracı profilden çıkar.
3. **Zaman penceresi.** Model START/STOP'u kendisi hesaplayıp UTC'ye daralttığında konsolun saat dilimi yüzünden 0 satır aldı. Prompt, pencereyi kopyalanmaya hazır AQL parçaları olarak verir.
4. **Küçük düzeltmeler (T-54).** Verification'ın hedef metni, Reporting'in özeti, `RunDeps` tutarlılığı, `ReportingTask`'in claim kanıtları.

## Okunacaklar

- `docs/decisions.md`: T-27, T-38, T-48, T-52, T-53, T-54, D-44; açık soru S-13
- `../ais0c-prs/PR-T-023.md` (lab bulguları, açık sorular 1–3), `../ais0c-prs/PR-T-047.md` (açık sorular 1–3)
- `packages/agents/src/ais0c_agents/runner.py`, `investigation.py`, `verification.py`, `reporting.py`
- Pydantic AI'nin `UsageLimits`'i ve `prepare_tools`'u (ya da eşdeğer bir capability)

## Branch

`agent/<araç>/T-048`, `main`'den (`f201c43` veya sonrası), **ayrı bir worktree'de** (`git worktree add ../ais0c-T-048 -b agent/<araç>/T-048 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `packages/agents/src/ais0c_agents/`: `runner.py`, `toolset.py` (yalnızca `RunDeps` doğrulayıcısı), `investigation.py`, `verification.py`, `reporting.py`
- `packages/agents/tests/`
- `prompts/investigation/v1.md`, `prompts/verification/v1.md`, `prompts/reporting/v1.md`: hiçbiri yayınlanmadı; yerinde değişir, manifest sürümleri aynı kalır
- `config/agents/investigation.yaml`: yalnızca `budgets.tokens`
- `skills/*/1.0.0/skill.yaml`: yalnızca `budgets.tokens` (üçü de taslak)
- `config/connectors/qradar.yaml`: yalnızca `qradar-investigate-read`'in araç listesi
- `services/mcp-gateway/tests/`: profilin araç listesine bağlı testler
- `packages/activities/src/ais0c_activities/settings.py` ve `packages/activities/tests/test_plan_settings.py`: yalnızca `AIS0C_PLAN_TOKENS`'ın varsayılanı
- `tests/e2e/test_lab_investigation.py`, `tests/e2e/test_lab_verification.py`

Bu dosyaların dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

`DataGap`, `DataGapReason.BUDGET_EXHAUSTED`, `AgentResult` alt tipleri, `Budget`, `TimeWindow`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir. Birim testleri gerçek model çağırmaz (`ScriptedModel`/`FunctionModel`).

1. **Son cevap (T-52).** Ortak runner, şu durumlardan biri olunca bir sonraki istekte fonksiyon araçlarını vermez; yalnızca çıktı aracı kalır:
   - kalan token bütçesi, bir önceki isteğin toplam token'ının iki katından az;
   - araç çağrısı bütçesi tükendi.

   O istek modele bütçenin bittiğini ve sonucu şimdi döndürmesi gerektiğini söyleyen kısa, sabit bir cümle taşır. Bu yolla biten çalışma `completed`'dır. Sonucuna kaynağı ajan, dönemi görevin penceresi olan ve nedeni `budget_exhausted` olan bir data gap eklenir; modelin kendi data gap'leri korunur.
   - Test: senaryolu bir modelle bütçe eşiğine gelen çalışmada son isteğin araç listesi boştur, sonuç döner ve data gap vardır.
   - Test: eşiğe gelmeyen çalışma değişmez (Triage'ın mevcut testleri aynen geçer).
   - Test: araçsız ajanlar (Orchestrator, Reporting) etkilenmez.
   - Model sonucu yine vermezse çalışma bugünkü gibi `budget_exhausted` ile biter.
2. **Bütçeler (T-52).**
   - `config/agents/investigation.yaml`: `budgets.tokens` 300000.
   - Üç taslak skill: `budgets.tokens` 250000.
   - `AIS0C_PLAN_TOKENS` varsayılanı 400000.
   - Test: manifest, skill ve ayar testleri yeni değerleri gösterir; plan varsayılanı Investigation'ın ve Verification'ın token bütçelerinin toplamını karşılar.
3. **Profil (T-52).** `qradar-investigate-read`'den `list_rules`, `list_log_sources`, `list_log_source_types` ve `list_offense_types` çıkar. Diğer profiller değişmez.
   - Test: gateway bu dört aracı investigate token'ıyla reddeder; Investigation ajanının modele gösterdiği araç listesinde yoktur.
4. **Zaman penceresi (T-53).** Investigation'ın ve Verification'ın prompt'u değerlendirme penceresini şu iki parçayla verir:
   - `starttime BETWEEN <başlangıç ms> AND <bitiş ms>`;
   - pencerenin iki yanından birer gün genişletilmiş `START '<yyyy-MM-dd HH:mm>' STOP '<yyyy-MM-dd HH:mm>'`.

   Prompt, modelin bu iki parçayı olduğu gibi kullanmasını ve saat çevirmemesini söyler. Prompt'taki örnek AQL bu parçalarla yazılır ve investigate profilinin Guard'ından geçer.
   - Test: verilen bir pencere için iki parçanın metni birebir beklenen değerdir; örnek AQL Guard'dan geçer; çift tırnak yoktur.
5. **Verification'ın hedefi (T-54 (1)).** Plan adımının `objective`'i Verification'ın prompt'unda `agent.objective` bloğundadır; kullanıcı prompt'u platformun sabit cümlesidir. Test: hedefteki kapanış etiketi bloktan kaçamaz ve hedef metni blok dışında geçmez.
6. **Reporting özeti (T-54 (2)).** `summary_tr` bir kanıt takma adı (`ev_c<n>`, `ev_<n>`, `ev_none`) taşırsa `ModelRetry` ile geri gönderilir. Prompt bunu söyler. Test: kabul ve red.
7. **Tutarlılık (T-54 (3), (4)).** `RunDeps.context_excerpts` boştur ya da `context_evidence` ile aynı uzunluktadır; aksi doğrulama hatasıdır. `ReportingTask`, her claim'in kanıtının `evidence`'ta olduğunu denetler. Test: her ikisinin reddi.
8. **Lab (planner'ın verdiği kapalı offense: `QRADAR_LAB_OFFENSE_ID=30`).**
   - `tests/e2e/test_lab_investigation.py` artık `completed` ister: sonuç vardır ve sonucun her kanıt kimliği gateway'in bu çalışma için kaydettiği bir kimliktir. Bütçeye takılan çalışma da `completed` olur (kriter 1); bu durum PR'a yazılır.
   - `tests/e2e/test_lab_verification.py` geçmeye devam eder.
   - PR'a her koşunun durumu, token'ı, süresi, araç çağrıları, satır sayıları ve `budget_exhausted` data gap'inin olup olmadığı yazılır. En az iki koşu yapılır.
   - Testler offense açmaz, kapatmaz, not yazmaz.

## Kapsam dışı

- Workflow bağlantısı (T-026)
- Bütçelerin ve prompt kalitesinin ölçümü (T-030)
- Konsolun saat dilimini bir ayar yapmak (S-13'ün cevabından sonra)

## Bağımlılıklar

- `main` `f201c43` (T-023, T-046, T-047 birleşik)

## Notlar

- T-026 bu görevle paralel çalışır. Çakışabilecek tek dosya `packages/activities/src/ais0c_activities/settings.py`'dir; bu görev orada yalnızca `AIS0C_PLAN_TOKENS`'ın varsayılanını değiştirir.
- Dev modelleri (D-44): `soc-reasoning` DeepSeek V4 Flash, `soc-verifier` Qwen 122B (`forced_tool_choice: false`).
- Dev stack'in `.env`'i ve secret'ları `../ais0c-T-012/deploy/compose/` ve `../ais0c-T-037/deploy/compose/` altındadır; LiteLLM'in config'i ana checkout'tan okunur.
- Lab kimlik bilgileri `~/.config/ais0c/lab.env` dosyasındadır. Repoya, fixture'a veya PR'a kopyalanmaz.
