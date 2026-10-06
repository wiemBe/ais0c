# T-049: Zincir düzeltmeleri: Ariel saat dilimi, son cevap, Verification penceresi, QA ve çalışma kaydı

## Amaç

T-026 ve T-048'in incelemesinden çıkan düzeltmeler (T-55–T-58):

1. **Ariel saat dilimi (T-55).** Lab'ın Ariel API'si START/STOP'u UTC okuyor; ajanların varsayılanı Europe/Istanbul. Worker kurucuya saat dilimini vermediği için zincirdeki her Investigation ve Verification araması lab'da yanlış saatleri tarar.
2. **Son cevap (T-56).** `FinalAnswer` her koşuda ekleniyor ve Pydantic AI'nin iç bayrağına dayanıyor. Ayrıca tek model isteği kaldığında araçlar geri çekilmiyor.
3. **Verification penceresi (T-56).** Vakanın penceresi offense başlangıcından şimdiye kadardır ve 2 saati hep aşar; hazır START/STOP parçası Verification profiline hiç sığmıyor.
4. **QA ve çalışma kaydı (T-57).** `qa_items.evaluation_no`, `agent_runs.error`; yeniden deneme ayarının adı.
5. **Dev skill'leri (T-58).** Depodaki skill'ler taslak; dev'de hiçbir plan skill'li koşamıyor.

## Okunacaklar

- `docs/decisions.md`: T-21, T-52, T-53, T-55, T-56, T-57, T-58
- `../ais0c-prs/PR-T-026.md` (sapmalar, açık sorular 1, 3, 4, 5) ve `../ais0c-prs/PR-T-048.md` (sapma 2, açık sorular 1, 2, 4)
- `docs/impl/data-model.md`: `qa_items`, `agent_runs`
- `packages/agents/src/ais0c_agents/runner.py`, `builder.py`; `packages/activities/src/ais0c_activities/agent_runtimes.py`, `runtime.py`, `settings.py`

## Branch

`agent/<araç>/T-049`, `main`'den (`c301e1e` veya sonrası), **ayrı bir worktree'de** (`git worktree add ../ais0c-T-049 -b agent/<araç>/T-049 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `packages/agents/src/ais0c_agents/`: `runner.py`, `builder.py`, `investigation.py`, `verification.py`, `triage.py` (yalnızca `FinalAnswer`'ın kurulumda bağlanması için)
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

1. **Ariel saat dilimi (T-55).**
   - `CaseSettings` `AIS0C_QRADAR_ARIEL_TIMEZONE`'u okur (IANA adı, `zoneinfo.ZoneInfo` ile doğrulanır). Ayar yoksa veya geçersizse `load_case_runtime` `RuntimeConfigError` verir; worker 2 ile çıkar.
   - Runtime, değeri `build_investigation_agent` ve `build_verification_agent`'a `console_zone` olarak verir.
   - Lab e2e testleri (`test_lab_triage.py`, `test_lab_investigation.py`, `test_lab_verification.py`) worker'a `UTC` verir; `deploy/compose/README.md` ayarı ve lab değerini anlatır.
   - **Ölçüm (lab, salt okunur):** gateway üzerinden bir sorguda START/STOP'u epoch milisaniyeyle yazmayı dene (`START <ms> STOP <ms>`). QRadar kabul ediyor mu, satırlar doğru mu? Sonuç PR'a yazılır. Guard bunu bugün reddediyorsa Guard değişmez; ölçüm yalnızca QRadar'ı doğrudan sorgulayan geçici bir betikle yapılır ve betik commit'lenmez.
   - Test: ayar yok, geçersiz, geçerli; kurulan ajanların prompt'u verilen dilimle yazılır.
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
- Prod'da saat diliminin ölçülmesi (H-8)

## Bağımlılıklar

- `main` `c301e1e` (T-026 ve T-048 birleşik)

## Notlar

- Lab testleri planner'ın verdiği kapalı offense'i okur (`QRADAR_LAB_OFFENSE_ID=30`); offense açmaz, kapatmaz, not yazmaz. Zincirin lab e2e'sini (`test_lab_triage.py`) planner koşar.
- Dev stack 2026-10-06'dan beri ana checkout'tan kurulur (`deploy/compose/.env` ve `secrets/` orada, git'te yok sayılır): `docker compose -p ais0c-dev -f deploy/compose/docker-compose.dev.yaml -f deploy/compose/docker-compose.lab.yaml --profile qradar up -d`. Görev worktree'sinden stack yeniden kurulmaz.
- T-045 bu görevden sonra verilir; ikisi de `packages/activities/` ve `services/worker/`'a dokunur.
