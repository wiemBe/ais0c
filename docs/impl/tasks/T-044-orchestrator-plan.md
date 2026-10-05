# T-044: Orchestrator ajanı ve plan doğrulama

## Amaç

İki parçayı yazmak:

- Triage'ın yapısal sonucundan ve aday skill'lerden tipli bir `CasePlan` üreten Orchestrator ajanı (architecture §7, "Orchestrator");
- planı workflow içinde deterministik olarak doğrulayan fonksiyon (T-41).

Orchestrator plan üretir, kendisi araştırmaz: aracı yoktur, ajanların konuşma metnini görmez. Workflow'a bağlanması T-026'dadır.

## Okunacaklar

- `docs/architecture.md` §7 (Orchestrator, Skill'ler: "Seçim"), §8.1, §8.3, §20
- `docs/impl/contracts.md`: `CasePlan`, `PlanStep`, `TriageResult`, `SkillRef`, `Budget`, `AgentTask`
- `docs/impl/prompts.md`
- `docs/decisions.md`: T-21, T-26, T-40, T-41, T-45
- `packages/knowledge/src/ais0c_knowledge/skills/` (`candidate_skills`, `SkillManifest`)
- T-043'ün PR'ı: araçsız ajan, skill metni

## İzinli dizinler

- `packages/agents/` (yeni `orchestrator` modülü ve testleri; dışa aktarımlar)
- `prompts/orchestrator/`
- `config/agents/orchestrator.yaml`
- `packages/workflows/src/ais0c_workflows/plan.py` (yeni modül) ve `packages/workflows/tests/` (yalnızca plan testleri)
- `packages/activities/src/ais0c_activities/settings.py` ve testleri: yalnızca plan bütçesi ayarları

Bu dizinlerin dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

- `CasePlan`, `PlanStep`, `TriageResult`, `SkillRef`, `Budget`, `TimeWindow`, `AgentTask`, `DataGap`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Manifest.** `config/agents/orchestrator.yaml`:

   | Alan | Değer |
   |---|---|
   | `id` | `orchestrator` |
   | `version` | `1.0.0` |
   | `workflow_types` | `[case]` |
   | `model_alias` | `soc-reasoning` |
   | `input_schema` | `OrchestratorTask` |
   | `output_schema` | `CasePlan` |
   | `toolset_profile` | `null` |
   | `max_steps` | 4 |
   | `budgets` | tokens 40000, tool_calls 0, wall_clock_seconds 90 |
2. **Girdi (T-45).** `OrchestratorTask` (`packages/agents`'ta) şunları taşır:

   | Alan | İçerik |
   |---|---|
   | `task` | `AgentTask` |
   | `triage` | Triage'ın kararı, güveni, seviyesi, `needs_investigation`, `investigation_focus`, data gap'leri ve `injection_suspected`. `rationale` ve claim metinleri yoktur. |
   | `offense` | Offense'in yapısal alanları |
   | `candidates` | Aday skill'ler; her biri `SkillRef`, ajan rolü, `required_evidence` açıklamaları ve bütçesi |
   | `agents` | Plan ajanları (Investigation, Verification) ve manifest bütçeleri |
   | `plan_budget` | Plan bütçesi |

   Prompt'taki yerleri:
   - aday skill'lerin manifest bilgisi onaylı içeriktir ve prompt'un parçasıdır;
   - `investigation_focus` ve offense alanları `untrusted_*` içindedir.

   Test: `FunctionModel`'in gördüğü mesajlarda `rationale` ve claim metni yoktur.
3. **Çıktı.** Model bir `CasePlan` (1–4 adım) döner. Ajanın kendi doğrulaması yalnızca şemadır; anlam kontrolü 4. maddedeki fonksiyonundur. Ajanın aracı yoktur (test).
4. **Plan doğrulama (T-41).** `ais0c_workflows.plan` modülü saf bir fonksiyon sunar. Modül yalnızca `ais0c_contracts`'ı ve standart kütüphaneyi import eder; G/Ç, saat ve rastgelelik yoktur. Fonksiyon planı, izinli ajanların bütçelerini, aday skill'leri, plan bütçesini ve Triage'ın `needs_investigation` değerini alır.
   - Döndürdüğü sonuç: uygulanacak adımlar, varsayılan planın kullanılıp kullanılmadığı ve red nedeni.
   - Kural 1–6'nın her biri için ayrı test vardır:
     - bilinmeyen ajan;
     - Triage, Orchestrator veya Reporting'in adım olması;
     - aynı ajanın iki kez geçmesi;
     - boş `objective`;
     - aday olmayan skill, başka rolün skill'i, listedekinden farklı sürüm;
     - eksik Verification'ın sona eklenmesi;
     - Verification'ın sona taşınması;
     - manifest ve skill bütçesiyle kırpma;
     - Verification'ın bütçesinin önce ayrılması;
     - dörtte birin altında kalan adımın düşmesi;
     - pencerenin kırpılması;
     - `needs_investigation` doğru ve yanlışken varsayılan plan.
   - Aynı girdi her zaman aynı sonucu verir.
5. **Plan bütçesi.** `CaseSettings` üç ayar kazanır:

   | Ortam değişkeni | Varsayılan |
   |---|---|
   | `AIS0C_PLAN_TOKENS` | 250000 |
   | `AIS0C_PLAN_TOOL_CALLS` | 40 |
   | `AIS0C_PLAN_SECONDS` | 480 |

   Pozitif tamsayı olmayan değer reddedilir. T-030 bu değerleri ölçer.

## Kapsam dışı

- Aday skill listesinin çıkarılması (router hazır, T-021) ve workflow'da kullanılması (T-026)
- Planın adımlarının çalıştırılması (T-026)

## Bağımlılıklar

- T-043

## Notlar

- Branch `main`'den açılır.
- Orchestrator geçersiz bir plan döndürürse vaka varsayılan planla devam eder (T-41). Bu yüzden ajanın hatası vakayı durdurmaz; prompt yine de kuralları açıkça yazar.
- Plan adımının `time_window`'u değerlendirmenin penceresi içinde kalmalıdır. Dışına taşan pencere kırpılır; kırpma bir red nedeni değildir.
- Dev'de `soc-reasoning` Space Bunny'dir (D-39, D-43). Yapısal çıktı sorunu T-023'te ölçülür; alias değişirse bu ajan da değişikliği kullanır.
