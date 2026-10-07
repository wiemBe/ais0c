# T-053: Harness: Orchestrator, Reporting ve Turkish Quality

## Amaç

T-030'un koşucusuna araçsız iki ajanı eklemek ve Türkçe kaliteyi ölçmek (T-64, D-07, D-44):

1. **Orchestrator adaptörü** ve suite'i. Offense 35'in zincirinde Orchestrator gerekçe vermeden `injection_suspected: true` dedi ve vaka QA'ya düştü (T-030 için biriken notlar, planner.md). Bu görev durumun sıklığını ölçer ve nedenini bulur.
2. **Reporting adaptörü** ve suite'i: `CaseReport`'un deterministik kuralları (T-50, T-54).
3. **Turkish Quality suite'i:** Reporting'in Türkçe özetinin kalitesi. Deterministik denetimlerin yanında bir LLM değerlendiricisi kullanılır. Sonuç registry'deki `turkish_quality` alanına yazılır (D-44).

İki ajanın da aracı yoktur. Girdileri önceki ajanların yapısal çıktısıdır. Bu yüzden `fixture` modu yeter; replay gerekmez.

## Tasarım (T-71, öneri)

- **Senaryo girdileri:** dev veritabanındaki gerçek zincir koşularından (offense 33–35, `agent_runs.task`) alınır. Adresler ve adlar T-052'nin kurallarıyla anonimleştirilir: RFC 5737 adresler, `example.com` adları; lab'ın sentetik kullanıcı adları (`svc_backup`) kalabilir. El yazımı senaryolar da eklenebilir.
- **Değerlendirici:** Turkish Quality'nin LLM değerlendiricisi `soc-reasoning` alias'ıdır. Değerlendirilen `soc-report`'tan farklı bir model ailesidir. Değerlendirici bir rubrikle 1–5 arası puan verir:
  - teknik doğruluk (özet, girdideki kararla ve kanıtla çelişmiyor);
  - Türkçe akıcılık ve dilbilgisi;
  - terim tutarlılığı (SOC terimleri yerleşik karşılıklarıyla veya İngilizce özgün haliyle, karışık değil);
  - belirsizliğin dürüst ifadesi (data gap'ler gizlenmiyor);
  - kısalık.
  
  Değerlendiricinin çıktısı yapısaldır (puanlar ve kısa gerekçe). LLM değerlendiricisi güvenlik gate'inin kararı olamaz (agent-harness §7); Turkish Quality bir kalite suite'idir.
- **Deterministik denetimler önce gelir:** özet en çok 400 karakter; kanıt takma adı (`ev_c<n>`, `ev_<n>`, `ev_none`) ve alan adı yok (T-54); metin Türkçe (Türkçe karakter ve sözcük oranı, İngilizce cümle yok); `_tr` alanları boş değil. Deterministik denetimden kalan özet değerlendiriciye gönderilmez, `fail` olur.

## Okunacaklar

- `docs/agent-harness.md` §6 (Turkish Quality), §7 (evaluator hiyerarşisi), §8
- `docs/decisions.md`: D-07, D-44, T-41, T-42, T-45, T-48, T-50, T-54, T-64, T-67, T-71
- `docs/impl/prompts.md`: Orchestrator ve Reporting bölümleri, `agent.*` kaynakları
- `harness/README.md` ("Eval runner"), `harness/src/ais0c_harness/eval/` (`adapter.py`, `triage.py`, `evaluate.py`, `suites.py`)
- `packages/agents/src/ais0c_agents/` (Orchestrator, Reporting kurucuları), `packages/workflows/src/ais0c_workflows/plan.py` (`validate_plan`)
- `../ais0c-prs/PR-T-030.md`, `PR-T-044.md`, `PR-T-047.md`
- `config/models/registry.dev.yaml`, `registry.prod.yaml` (`turkish_quality`)

## Branch

`agent/<araç>/T-053`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-053 -b agent/<araç>/T-053 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `harness/` (kod, testler, `suites/`, README)
- `config/agents/orchestrator.yaml`, `config/agents/reporting.yaml`: yalnızca `eval_suites`
- `config/models/registry.dev.yaml`: yalnızca `soc-report`'un `turkish_quality` alanı (ölçülen değer)

Bu dosyaların dışında hiçbir dosya değiştirilmez. Prompt'lar değişmez: Orchestrator'ın `injection_suspected`'ı için bir düzeltme gerekiyorsa PR'da önerilir, ayrı bir görev olur.

## Kullanılan sözleşmeler

`CasePlan`, `CaseReport`, `UrgentEvent`, `AgentResult`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir. Kriter 6 dışındaki testler gerçek model çağırmaz.

1. **Orchestrator adaptörü.** Görevi worker'daki gibi kurar: Triage'ın sonucu `agent.*` kaynaklarıyla, aday skill'ler, plan bütçesi. Çıktı `validate_plan`'dan geçer. Değerlendirme:
   - plan geçerli mi ve `validate_plan` onu değiştirmeden kabul ediyor mu;
   - beklenen ajanlar planda mı;
   - `injection_suspected` beklenenle aynı mı.
   - Test: scripted modelle geçerli plan, geçersiz plan (düzeltilmiş), beklenmeyen `injection_suspected`.
2. **Orchestrator suite'i** (`orchestrator-gold`, `kind: quality`):
   - `orc-01-dcsync-chain`: offense 35'in Orchestrator girdisi, anonimleştirilmiş; beklenen `injection_suspected: false`;
   - `orc-02-injection-in-claim`: Triage'ın claim metninde bir talimat; beklenen `injection_suspected: true`;
   - `orc-03-skill-candidate`: aday skill'li bir girdi; plan skill'i Investigation adımına bağlar.
3. **Reporting adaptörü ve suite'i** (`reporting-gold`, `kind: quality`). Görevi worker'daki gibi kurar (aday numaraları, `ev_c<n>`, T-50). Deterministik değerlendirme:
   - acil event'ler adaylardan numarayla seçilmiş, `rank` ardışık;
   - özet en çok 400 karakter, kanıt takma adı ve alan adı yok;
   - önerilerin aksiyon türleri geçerli.
   - En az üç senaryo: `tp`, `fp` ve data gap'li bir karar.
4. **Turkish Quality suite'i** (`turkish-quality`, `kind: quality`). Reporting'in çıktısını önce deterministik denetimlerden, sonra değerlendiriciden geçirir. Senaryo başına puanlar ve ortalama raporlanır; geçme eşiği ortalama ≥ 4 ve hiçbir ölçütte 2'nin altı yok.
   - Değerlendiricinin prompt'u ve rubriği harness'te, sürümlüdür; sürümü raporun zarfına girer.
   - Test: değerlendirici scripted modelle; deterministik denetimden kalan özetin değerlendiriciye gitmemesi; bozuk değerlendirici çıktısının `error` olması.
   - Negatif test: İngilizce bir özet, takma adlı bir özet ve 401 karakterlik bir özet deterministik denetimde kalır.
5. **Kayıtlardan senaryo.** Dev veritabanından bir zincir koşusunun girdisini senaryo dosyasına çeviren yardımcı komut veya betik (anonimleştirme T-052'nin kurallarıyla; T-052 henüz birleşmediyse aynı kurallar burada yazılır, ikinci birleşen birleştirir). Repo testi: senaryolarda RFC 5737 ve `2001:db8::/32` dışında IP adresi yok.
6. **Gerçek model ölçümü.** Üç suite k=5 ile bir kez koşulur (dev stack'in LiteLLM'i). Rapor `../ais0c-prs/T-053-reports/`'a, özet PR'a yazılır:
   - Orchestrator: `orc-01`'de `injection_suspected` oranı. Sıfır değilse, koşu dosyalarından modelin hangi girdiye tepki verdiği ve bir düzeltme önerisi (prompt veya girdi kurgusu) PR'a yazılır.
   - Reporting: deterministik denetim geçme oranı.
   - Turkish Quality: ölçüt başına ortalama puan. `registry.dev.yaml`'daki `soc-report`'un `turkish_quality`'si bu sonuçla doldurulur (suite sürümü, tarih ve ortalama puan, registry'nin yorumundaki biçimle).
7. **Manifest'ler.** `orchestrator.yaml` ve `reporting.yaml`'ın `eval_suites`'i yeni suite'leri listeler; `releases` komutu onları gösterir.

## Kapsam dışı

- Replay, Investigation ve Verification (T-052)
- Prompt değişiklikleri (öneri PR'da)
- Prod modelleriyle ölçüm (shadow öncesi, T-031 ile)

## Bağımlılıklar

- `main` `9a47ab0` veya sonrası (T-030 dahil)
- T-052 ile paralel yürür; ikisi de `ADAPTERS`'a ekler. İkinci birleşen çakışmayı çözer.

## Notlar

- Dev veritabanına ana checkout'taki `deploy/compose/.env` ile bağlanılır; yalnızca okunur.
- Gerçek model koşusu dev stack'in LiteLLM'ini kullanır (ana checkout'tan `--no-deps litellm`). `OPENROUTER_API_KEY`'in boş olmadığını uzunluğuyla kontrol et.
- Registry'deki `turkish_quality` alanının biçimi dosyanın başındaki yorumdadır; biçim belirsizse PR'da sorulur.
