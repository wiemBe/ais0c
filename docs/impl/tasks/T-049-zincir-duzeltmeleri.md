# T-049: Zincir düzeltmeleri: Ariel zaman sınırı, son cevap, Verification penceresi, QA ve çalışma kaydı

## Amaç

T-026 ve T-048'in incelemesinden çıkan düzeltmeler (T-55–T-58):

1. **Ariel zaman sınırı (T-55).** Lab'ın Ariel API'si START/STOP metnini UTC okuyor; ajanların varsayılanı Europe/Istanbul, bu yüzden zincirdeki aramalar lab'da yanlış saatleri tarıyor. Bu görevin lab ölçümü (2026-10-06) QRadar'ın epoch milisaniyeli START/STOP'u kabul ettiğini gösterdi; ajanlar saat dilimi yerine milisaniye kullanır.
2. **Son cevap (T-56).** `FinalAnswer` her koşuda ekleniyor ve Pydantic AI'nin iç bayrağına dayanıyor. Ayrıca tek model isteği kaldığında araçlar geri çekilmiyor.
3. **Verification penceresi (T-56).** Vakanın penceresi offense başlangıcından şimdiye kadardır ve 2 saati hep aşar; hazır START/STOP parçası Verification profiline hiç sığmıyor.
4. **QA ve çalışma kaydı (T-57).** `qa_items.evaluation_no`, `agent_runs.error`; yeniden deneme ayarının adı.
5. **Dev skill'leri (T-58).** Depodaki skill'ler taslak; dev'de hiçbir plan skill'li koşamıyor.

## Okunacaklar

- `docs/decisions.md`: T-21, T-39, T-52, T-55, T-56, T-57, T-58 (T-53'ün yerini T-55 aldı)
- `../ais0c-prs/PR-T-026.md` (sapmalar, açık sorular 1, 3, 4, 5) ve `../ais0c-prs/PR-T-048.md` (sapma 2, açık sorular 1, 2, 4)
- `docs/impl/data-model.md`: `qa_items`, `agent_runs`
- `packages/agents/src/ais0c_agents/runner.py`, `builder.py`; `packages/activities/src/ais0c_activities/agent_runtimes.py`, `runtime.py`, `settings.py`

## Branch

`agent/<araç>/T-049`, `main`'den (`c301e1e` veya sonrası), **ayrı bir worktree'de** (`git worktree add ../ais0c-T-049 -b agent/<araç>/T-049 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `packages/agents/src/ais0c_agents/`: `runner.py`, `builder.py`, `investigation.py`, `verification.py`, `triage.py` (yalnızca `FinalAnswer`'ın kurulumda bağlanması için)
- `packages/policy/src/ais0c_policy/aql_guard.py` ve `packages/policy/tests/`
- `prompts/investigation/v1.md`, `prompts/verification/v1.md` (yayınlanmadı; yerinde değişir)
- `services/mcp-gateway/tests/`: Guard'ın yeni biçimine bağlı testler
- `packages/activities/`
- `packages/workflows/`: yalnızca yeniden deneme ayarının adı
- `packages/storage/`: migration `0008`, `qa_items` ve `agent_runs` repository'leri
- `packages/knowledge/src/ais0c_knowledge/skills/`: router'ın dev modu
- `services/worker/`
- `deploy/compose/README.md`
- `tests/e2e/`
- Bu paketlerin testleri

Bu dosyaların dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

`QAReason`, `RunStatus`, `SkillRef`, `TimeWindow`, `EvidenceRef`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Ariel zaman sınırı (T-55).**
   - **AQL Guard** sayısal START/STOP'u kabul eder: `START <ms> STOP <ms>`. İki değer de epoch milisaniyedir (13 hane veya daha uzun tamsayı), STOP START'tan büyüktür ve fark profilin penceresine sığar. Pencere başlangıcı ve bitişi UTC'li `datetime` olur. Metin biçimli START/STOP bugünkü gibi geçerlidir.
   - Negatif testler: saniye cinsinden değer (`START 1791208620 STOP 1791215940`), negatif veya sıfır, ondalıklı, STOP ≤ START, profili aşan fark, biri metin biri sayı olan karışık yazım, `LIMIT`'in zaman sınırından sonra gelmesi (T-39 kuralı sayısal biçimde de geçerli). Hepsi `time_bound_invalid`, `window_exceeds_profile` veya `limit_after_time_bound` ile reddedilir.
   - **Prompt'lar.** Investigation ve Verification'ın `time_window` bölümü değerlendirme penceresini kopyalanmaya hazır tek parça olarak verir: `START <başlangıç ms> STOP <bitiş ms>`. Pay ve `starttime BETWEEN` filtresi kalkar. Örnek AQL bu parçayla yazılır ve profilin Guard'ından geçer. Model saat çevirmez ve sayıları değiştirmez.
   - **Uzun pencere.** Pencere profilin sınırını aşıyorsa (Verification: 2 saat) prompt, pencereyi baştan itibaren sınıra sığan ardışık hazır parçalar olarak verir (en çok 6 parça; sonrası yazılmaz ve bu da söylenir). Model parçalardan birini olduğu gibi kopyalar.
   - **Saat dilimi kalkar.** `console_zone` parametresi, `CONSOLE_ZONE` ve `aql_window`'un saat dilimi mantığı (T-048) kaldırılır. `AIS0C_QRADAR_ARIEL_TIMEZONE` ayarı eklenmez. Lab testleri artık saat dilimi ölçmez.
   - **Ölçüm (yapıldı):** `START 1791208620000 STOP 1791215940000` lab'da kabul edildi ve offense 30'un doğru 3 satırını döndürdü. PR'a yazılır. Değişiklik bittikten sonra lab testleri (`test_lab_investigation.py`, `test_lab_verification.py`) gateway üzerinden, Guard'dan geçen milisaniyeli sorgularla koşulur; satır sayıları PR'a yazılır.
   - Test: hazır parçanın metni birebir beklenen değerdir; uzun pencerenin parçaları ardışık ve sınıra sığar; örnek AQL Guard'dan geçer; prompt'ta saat dilimi adı yoktur.
2. **Son cevap (T-56).**
   - `FinalAnswer` `create_agent`'ta bağlanır; `run_agent` onu artık koşu başına eklemez ve `_safe_at_runtime` kullanılmaz.
   - Üçüncü koşul: kalan model isteği hakkı bir olduğunda (`max_steps`) araçlar geri çekilir.
   - T-048'in `test_final_answer.py` testleri ve case worker'ın Temporal testleri geçer. Test: tek istek hakkı kalan koşu sonucu döndürür ve `budget_exhausted` data gap'i taşır.
3. **Verification penceresi (T-56).** Verification'ın `AgentTask.time_window`'u, ona giden claim'lerin kanıtlarının pencerelerinin birleşimidir (en erken başlangıç, en geç bitiş), vakanın değerlendirme penceresine kırpılır. Kanıtın penceresi yoksa vakanın penceresi kullanılır.
   - Test: birleşim, kırpma, penceresiz kanıt; lab koşusunda (T-024'teki gibi) hazır START/STOP parçasının profilin Guard'ından geçmesi.
4. **QA ve çalışma kaydı (T-57).**
   - Migration `0008`: `qa_items.evaluation_no int NOT NULL` (mevcut satırlar için vakanın son değerlendirme numarası) ve `(case_id, evaluation_no, reason)` üzerinde tekil indeks; `agent_runs.error text NULL`. Upgrade ve downgrade verili bir veritabanında koşar.
   - `record_decision` QA satırlarını değerlendirme numarasıyla yazar. `finish_agent_run`, başarısız veya bütçeye takılan çalışmanın nedenini `error`'a yazar (en çok 2000 karakter, kesilir).
   - `AIS0C_TRIAGE_RETRY_MINUTES` → `AIS0C_AGENT_RETRY_MINUTES`, `triage_retry_delay` → `agent_retry_delay` (activity adı dahil; iki `names.py` listesi eşleşir).
   - Test: tekillik, migration, `error` kaydı, yeni ad.
5. **Dev skill'leri (T-58).** `AIS0C_SKILLS_MODE=dev` iken router taslak skill'leri de aday listeler; `prod`'da listelemez. Lab e2e (`AIS0C_SKILLS_MODE=dev`) `windows-dcsync`'in plana girebildiğini gösterebilir.
   - Test: aynı vaka dev'de taslak adayı alır, prod'da almaz.

## Kapsam dışı

- Executor'ın akışa bağlanması (T-045)
- Bütçelerin ve eşiğin ölçümü (T-030)

## Bağımlılıklar

- `main` `c301e1e` (T-026 ve T-048 birleşik)

## Notlar

- Lab testleri planner'ın verdiği kapalı offense'i okur (`QRADAR_LAB_OFFENSE_ID=30`); offense açmaz, kapatmaz, not yazmaz. Zincirin lab e2e'sini (`test_lab_triage.py`) planner koşar.
- Dev stack 2026-10-06'dan beri ana checkout'tan kurulur (`deploy/compose/.env` ve `secrets/` orada, git'te yok sayılır): `docker compose -p ais0c-dev -f deploy/compose/docker-compose.dev.yaml -f deploy/compose/docker-compose.lab.yaml --profile qradar up -d`. Görev worktree'sinden stack yeniden kurulmaz.
- T-045 bu görevden sonra verilir; ikisi de `packages/activities/` ve `services/worker/`'a dokunur.
- **Worktree:** Codex bu görevi `/tmp/ais0c-T-049`'da başlattı. `/tmp` yeniden başlatmada silinir: önce commit'lenir, sonra worktree `git worktree move /tmp/ais0c-T-049 ../ais0c-T-049` ile taşınabilir.
