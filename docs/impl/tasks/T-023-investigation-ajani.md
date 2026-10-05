# T-023: Investigation ajanı

## Amaç

Triage'ın derin araştırma gerektirdiğini söylediği vakalarda kararı derinleştiren ajanı yazmak (architecture §7). Ajan şunları yapar:

- hedefli AQL sorguları çalıştırır;
- timeline ve hipotez çıkarır;
- operatöre acil event adayları önerir;
- Orchestrator bir skill seçtiyse skill'in yöntemini izler.

Ajan workflow'a T-026'da bağlanır. Bu görevde birim testleri ve lab'da Temporal'sız bir koşu vardır.

## Okunacaklar

- `docs/architecture.md` §7 (Ajanlar, Skill'ler), §9 ("Acil bakılması gereken event'ler"), §11.3, §13, §22
- `docs/impl/prompts.md` (tamamı)
- `docs/impl/contracts.md`: `InvestigationResult`, `TimelineEntry`, `UrgentEvent`, `AgentTask`, `PlanStep`
- `docs/decisions.md`: D-32, T-21, T-26, T-27, T-31, T-36, T-38, T-39, T-44, T-45
- `skills/README.md`, `skills/windows-dcsync/1.0.0/`
- T-043'ün PR'ı: ortak doğrulayıcı, bağlam kanıtı ve skill bölümünün kullanımı

## İzinli dizinler

- `packages/agents/` (yeni `investigation` modülü ve testleri; dışa aktarımlar)
- `prompts/investigation/`
- `config/agents/investigation.yaml`
- `tests/e2e/`: yalnızca bu görevin lab testi ve gerekirse ona ait yardımcı dosya

Bu dizinlerin dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

- `InvestigationResult`, `TimelineEntry`, `InvestigationHypothesis`, `UrgentEvent`, `Claim`, `DataGap`, `AgentTask`, `OffenseSnapshot`, `EnrichmentContext`, `TriageResult`, `EvidenceRef`, `SkillRef`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir. Testler gerçek model ve gerçek QRadar kullanmaz; `FunctionModel`/`TestModel` ve sahte gateway ile yazılır (7. madde hariç).

1. **Manifest.** `config/agents/investigation.yaml`:

   | Alan | Değer |
   |---|---|
   | `id` | `investigation` |
   | `version` | `1.0.0` |
   | `workflow_types` | `[case]` |
   | `model_alias` | `soc-reasoning` |
   | `input_schema` | `InvestigationTask` |
   | `output_schema` | `InvestigationResult` |
   | `toolset_profile` | `qradar-investigate-read` |
   | `max_steps` | 30 |
   | `budgets` | tokens 150000, tool_calls 24, wall_clock_seconds 300 |
   | `prompt` | `prompts/investigation/v1.md` |
   | `shared_rules` | `prompts/_shared/rules/v2.md` |

   Bütçeler başlangıç değeridir; T-030 ölçer. Manifest model registry'ye karşı doğrulanır.
2. **Girdi.** `InvestigationTask` (`packages/contracts`'ta değil, `packages/agents`'ta) şunları taşır:

   | Alan | İçerik |
   |---|---|
   | `task` | `AgentTask`; `objective`, plan adımının hedefidir |
   | `offense` | `OffenseSnapshot` |
   | `enrichment` | `EnrichmentContext` |
   | `triage` | Triage'ın yapısal sonucu: karar, güven, seviye, `investigation_focus`, claim'ler ve data gap'ler. `rationale` yoktur (T-45). |
   | `context_evidence` | Triage'ın claim'lerinin kanıtı, en fazla 30 |
   | `skill` | T-043'ün skill girdisi veya yok |
   | `knowledge` | Dış bilgi, en fazla 10 |

   Prompt'taki güven katmanları:
   - offense, Triage'ın claim metinleri ve `investigation_focus` `untrusted_*` içindedir;
   - katalog ve kritik varlıklar `org_context`'tedir;
   - skill prompt'un parçasıdır (T-44);
   - bağlam kanıtı `ev_c<n>`'dir (T-38);
   - taban seviye ve `group_id` prompt'a girmez (T-31).

   Test: `FunctionModel`'in gördüğü mesajlarda her parça kendi katmanındadır ve Triage'ın `rationale`'ı hiçbir yerde geçmez.
3. **Araçlar.** Ajan yalnızca `qradar-investigate-read` profilinin araçlarını görür.
   - Prompt, Ariel akışını anlatır: `create_ariel_search`, ardından `get_ariel_search_status` (`wait_seconds` ile), `get_ariel_search_results` ve `delete_ariel_search`. Prompt ayrıca araç bütçesini söyler.
   - Test (sahte gateway): bir sorgu dört çağrıyla tamamlanır ve her `ToolIntent`'in `run_id`'si çalışmanınkidir. Profil dışındaki bir araç çağrılamaz.
4. **Çıktı.** Çıktı `InvestigationResult`'tır.
   - Bütün kanıt alanları T-043'ün doğrulayıcısından geçer (claim, timeline, acil event).
   - Acil event AQL'i T-39'a göre denetlenir.
   - `urgent_event_candidates`'in `rank` değerleri 1'den başlar, ardışık ve benzersizdir. Aksi `ModelRetry` ile geri gönderilir.
   - Test: her biri için kabul ve red.
5. **Skill.**
   - Skill verilirse çalışmanın bütçesi manifest'in, görevin ve skill'in bütçesinin en küçüğüdür.
   - Skill'li ve skill'siz prompt'un Skill bölümü T-043'ün verdiği metindir.
   - Skill'in `allowed_agent_roles`'ü `investigation`'ı içermiyorsa ajan çalışmaya başlamadan hatayla durur. Workflow'daki asıl kontrol T-026'dadır.
6. **Güvenlik (negatif testler).**
   - Araç sonucuna gömülü talimat ve kapanış etiketi sarmalayıcıdan kaçamaz.
   - Ajanın araç listesinde yazma aracı yoktur.
   - `injection_suspected` modelin çıktısından gelir ve kaybolmaz.
7. **Lab testi (`@pytest.mark.lab`, `tests/e2e/`).** Test, dev stack'in gateway'i ve gerçek model ile ajanı Temporal'sız bir kez koşar.
   - Hedef, planner'ın verdiği **kapalı** bir lab offense'idir: `QRADAR_LAB_OFFENSE_ID`. Değişken yoksa test nedeniyle atlanır.
   - Çalışma önce storage'a kaydedilir, çünkü gateway yalnızca kayıtlı çalışmaların çağrısını alır.
   - Skill olarak `windows-dcsync` taslağı doğrudan verilir; router taslak önermez.
   - Test offense açmaz, kapatmaz, not yazmaz.
   - PR'a yazılanlar:
     - araç çağrısı sayısı;
     - AQL'lerin kaçının Guard'dan, kaçının QRadar'dan (422) döndüğü;
     - çift tırnaklı alan adlarının JSON'da bozulup bozulmadığı (T-36 (3));
     - token, süre ve sonuç.
   - Dev'de `soc-reasoning` DeepSeek V4 Flash'tır (D-44). Model config'i bu görevde değişmez; yapısal çıktı sorunu çıkarsa ölçüm PR'a yazılır.

## Kapsam dışı

- Workflow'a bağlanma, child workflow, run kaydında skill (T-026)
- Orchestrator ve plan doğrulama (T-044)
- Skill onayı ve harness suite'leri (T-030)
- Falcon toolset'i (Faz 2)

## Bağımlılıklar

- T-043

## Notlar

- Branch `main`'den açılır. İkinci bir `tests/__init__.py` eklenmez.
- AQL'de `LIMIT` zaman ifadesinden önce gelir. Gateway'in AQL Guard'ı pencereyi 7 gün, satırı 1000 ile sınırlar.
- Prompt'ta araç açıklamalarındaki filtre örnekleri tek tırnak kullanır (T-36 (3)).
- Lab kimlik bilgileri `~/.config/ais0c/lab.env` dosyasındadır. Repoya, fixture'a veya PR'a kopyalanmaz.
- Dev stack'in `.env`'i ve secret'ları `../ais0c-T-012/deploy/compose/` altındadır. Gateway token'ı `secrets/agents/gateway-token-qradar-investigate-read` dosyasındadır.
- Lab offense'lerini yalnızca planner açar ve kapatır.

## Devam (2026-10-06)

İlk ajan (OpenCode, Space Bunny) görevi bitiremedi: model OpenRouter'dan kalktı (D-44). Yarım iş commit'lenmeden ana checkout'ta kalmıştı; planner onu ayrı bir worktree'ye taşıdı. Yedeği `../ais0c-prs/backups/` altında.

- **Worktree:** `../ais0c-T-023`, branch `agent/opencode/T-023`. Değişiklikler commit'lenmemiştir. Yeni ajan bu worktree'den devam eder ve branch adını korur ya da `agent/<araç>/T-023` açıp dosyaları taşır.
- **Durum:** `investigation.py`, manifest, prompt v1 ve yedi test dosyası yazılmış; 187 birim testi geçiyor. Ajan bir kez kuruluyor (doğru).
- **Eksikler:**
  - `ruff check`'te 65+, `pyright`'ta 28 hata var.
  - Lab testi yanlış yerde: `packages/agents/tests/e2e/` ve yeni bir `__init__.py`. Test `tests/e2e/test_lab_investigation.py`'ye taşınır, `packages/agents/tests/e2e/` silinir. Lab testi `helpers`'ı içe aktaramıyor.
  - Kaynak adları T-48'e göre değişir: `investigation_focus` → `agent.focus`, Triage'ın claim'leri → `agent.claim`. Bunun için T-046 birleşmiş olmalı; branch T-046'dan sonra `main`'e rebase edilir.
  - Kriter 4'ün `rank` doğrulaması ve kriter 5'in skill rol kontrolü testlerle gösterilmeli; taslakta `output_validator` yok.
  - PR metni `../ais0c-prs/PR-T-023.md`'ye yazılır.
- **Lab:** `QRADAR_LAB_OFFENSE_ID=30` (kapalı, `svc_backup` DCSync). Test offense açmaz, kapatmaz, not yazmaz.

