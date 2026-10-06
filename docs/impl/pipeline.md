# Görev Pipeline'ı: Faz 0 kapanışı, Faz 1 ve canary öncesi

> Son güncelleme: 2026-10-05 (T-28–T-37 kararları; T-040–T-042 bitti; H-3 yapıldı, H-2 ertelendi; dalga B görev dosyaları ve T-38–T-46; H-7). Faz 0 görevleri (T-001–T-012) [multi-agent-dev.md](multi-agent-dev.md)'dedir. Bu pipeline, 2026-10-03 kararlarını (D-29–D-38, T-18–T-25) ve `future.md`'den alınan maddeleri koda taşır.

Çalışma kuralları [multi-agent-dev.md](multi-agent-dev.md) ile aynıdır: bir görev, bir branch, bir worktree; sözleşme değişikliği insan onayı ister; PR'ı farklı model ailesi inceler.

Görev dosyaları dalga dalga yazılır. Bir dalganın dosyaları, önceki dalga merge edildikten sonra o dalganın gerçek sonuçlarına göre yazılır. Bu tabloda dosyası olan görevler hemen başlayabilir; olmayanlar sıradaki dalgadır.

## İnsan adımları

Bunlar kodlama ajanına verilmez; kullanıcı yapar veya açık onayla yaptırır.

| Adım | İş | Ne zaman |
|---|---|---|
| H-1 | T-012'yi bitirmek: lab DCSync kuralının yüklenmesi (zip hazır), OpenRouter anahtarının `../ais0c-T-012/deploy/compose/.env`'e girilmesi, e2e testinin koşulması ve T-012'nin commit'lenmesi | Hemen |
| H-2 | `main`'deki kodun (Faz 0, dalga A ve T-038–T-042; `1ee6486`'ya kadar) farklı model ailesinden bir ajanla (Codex veya OpenCode + DeepSeek/GLM) incelenmesi | İleride; kullanıcı 2026-10-05'te H-3'ü öne aldı |
| H-3 | Dokümanların commit'lenmesi ve Faz 0 branch'lerinin `main`'e alınması | Yapıldı (2026-10-05): `main` entegrasyon branch'inin ucuna (`1ee6486`) ilerletildi, dokümanlar commit'lendi. Yeni görev branch'leri `main`'den açılır. |
| H-4 | ContextForge açıklarının IBM'e sorumlu bildirimle iletilmesi (T-18) | Uygun bir zamanda |
| H-5 | Lab QRadar'ın hazırlanması: custom property'ler, log source'lar, test kuralları; yalnızca not yazabilen ikinci bir lab token'ı (QRadar rollerinin not eklemeyi offense kapatmadan ayırıp ayıramadığını görmek için, architecture §11.2); IP'si olan en az bir varlık (T-039'un varlık testi atlanıyor) | Faz 1 lab testlerinden önce |
| H-6 | S-03 (giriş kaynağı), S-06 (audit saklama süresi) ve S-12 (e-posta relay'i) sorularının cevaplanması | Canary öncesi; S-12 T-031'den önce |
| H-7 | Prod model kaydının doldurulması: elle tutulan alanlar (`artifact_hash`, `quantization`, `tokenizer`, parser'lar, `inference_params`) prod sunucularda okunup `registry.prod.yaml`'a yazılır; `python -m ais0c_worker.model_release verify` 0 dönene kadar shadow başlamaz (T-32) | T-031 ile, shadow öncesi |

## Faz 0 kapanışı

| Görev | Kapsam | İzinli dizinler | Bağımlı olduğu | Dosya |
|---|---|---|---|---|
| T-013 | contracts v0.2: `ToolIntent.run_id`, `PlanStep` skill alanları, `ModelRelease`, `SkillRef`; `X-Ais0c-Run-Id` geçici çözümünün kaldırılması | `packages/contracts/`, `packages/agents/`, `services/mcp-gateway/`, `packages/activities/` | H-1 | [T-013](tasks/T-013-contracts-v02.md) |
| T-014 | Faz 0 kararlarının koda uygulanması: yeniden değerlendirme kuralı (D-31), boş intake okumalarında kanıt yazılmaması ve triage'ın bir kez yeniden denenmesi (D-33) | `packages/workflows/`, `packages/activities/`, `services/mcp-gateway/` | H-1 | [T-014](tasks/T-014-faz0-kararlari.md) |
| T-038 | Kanıt takma adları (T-27): model gerçek `evidence_id` yerine çalışma içi `ev_<n>` takma adını görür ve onunla atıf yapar; eşleme mesaj geçmişinden türetilir, çıktıda gerçek kimliğe çevrilir. Model 32 karakterlik kimlikleri yanlış kopyalıyor (lab e2e, 2026-10-05). | `packages/agents/`, `services/worker/tests/`, `services/mcp-gateway/tests/`, `tests/e2e/` | T-012 | [T-038](tasks/T-038-kanit-takma-adlari.md) |
| T-039 | QRadar araç açıklamalarında filtre örnekleri: tırnaklı IP değerleri, `list_assets` için iç içe IP filtresi; lab koşularında 8–9 çağrının 3'ü 422 aldı (2026-10-05) | `config/connectors/`, `services/mcp-gateway/tests/`, `tests/e2e/` | Entegrasyon branch'i | [T-039](tasks/T-039-qradar-filtre-ornekleri.md) |

## Dalga A sonrası düzeltmeler

Dalga A PR'larının açık sorularından çıkan görevler (T-28–T-37). Üçü de bitti ve `main`'de (2026-10-05).

| Görev | Kapsam | İzinli dizinler | Bağımlı olduğu | Dosya |
|---|---|---|---|---|
| T-040 | Fork ve gateway'in QRadar'la hizalanması (T-34): `note_text` form gövdesiyle, gateway'de UTF-16 sayımı, referans araçlarından `filter`'ın kalkması | `../qradar-mcp` (not ve referans araçları, snapshot'lar, testler), `config/connectors/qradar.yaml`, `services/mcp-gateway/` | Entegrasyon branch'i | [T-040](tasks/T-040-fork-gateway-hizalama.md) |
| T-041 | Sözleşmeler v0.4 ve veri modeli (T-37): `disabled` durumu, `notifications.error`, `missing_since`, `qradar_enabled`, `RunId` deseni, `label` sınırı; `update_offense_seen(first_seen_at)` (T-30) | `packages/contracts/`, `packages/storage/`, `packages/executor/`, `packages/knowledge/`, `packages/activities/`, `services/mcp-gateway/` | Entegrasyon branch'i | [T-041](tasks/T-041-sozlesmeler-v04.md) |
| T-042 | T-040 ve T-041'den kalan eskimiş metinler: fork README/NOTICE, not politikasının yorumu, iki testin sahte ret metni, KnowledgeSync testinin sayaç anahtarları | Fork `README.md`, `NOTICE`; `config/policies/qradar.yaml` (yorum), üç test dosyası | Entegrasyon branch'i | [T-042](tasks/T-042-eskimis-metinler.md) |

## Faz 1, dalga A (Kasım başı; T-013'ten sonra paralel)

| Görev | Kapsam | İzinli dizinler | Bağımlı olduğu | Dosya |
|---|---|---|---|---|
| T-015 | Güven katmanları: ortak kurallar v2, dış bilginin sarılması, `org_context`'in yalnızca olgu taşıması (T-20) | `prompts/_shared/`, `packages/policy/`, `packages/agents/` | T-013 | [T-015](tasks/T-015-guven-katmanlari.md) |
| T-016 | Model çalışma kaydı (T-24) | `config/models/`, `packages/storage/`, `packages/activities/`, `services/worker/` | T-013 | [T-016](tasks/T-016-model-kaydi.md) |
| T-017 | Platform bayrakları ve kill switch (T-23) | `packages/storage/`, `packages/executor/` | T-013 | [T-017](tasks/T-017-kill-switch.md) |
| T-018 | Gateway'in compose servisi ve `qradar-note-write` profili; `qradar-mcp-note` instance'ı | `services/mcp-gateway/`, `config/connectors/`, `config/policies/`, `deploy/compose/` | T-013 | [T-018](tasks/T-018-gateway-compose-not-profili.md) |
| T-019 | Executor: QRadar offense notu | `packages/executor/`, `packages/activities/` | T-017, T-018 | [T-019](tasks/T-019-executor-qradar-notu.md) |
| T-020 | Executor: e-posta bildirimi | `packages/executor/`, `packages/activities/`, `deploy/compose/` | T-017 | [T-020](tasks/T-020-executor-eposta.md) |
| T-021 | Skill altyapısı: format, yükleyici, router ve ilk üç skill'in taslakları (T-21) | `skills/`, `packages/knowledge/` | T-013 | [T-021](tasks/T-021-skill-altyapisi.md) |
| T-022 | Analiz Kataloğu senkronu: QRadar'dan kural ve log source listeleri (D-25) | `packages/knowledge/`, `packages/workflows/`, `packages/activities/`, `config/connectors/`, `config/policies/` | T-013 | [T-022](tasks/T-022-katalog-senkronu.md) |

T-019 ve T-020 aynı anda `packages/executor/` içinde çalışır. Çakışmayı önlemek için T-019 `note` modülüne, T-020 `email` modülüne dokunur; ortak parçaları (şablon yükleme, kill switch kontrolü) T-017 hazırlar.

## Faz 1, dalga B (dalga A'dan sonra)

Görev dosyaları 2026-10-05'te yazıldı. Pipeline'daki tek T-026 satırı dört göreve bölündü (T-40): T-043, T-044, T-026 ve T-045. Branch'ler `main`'den açılır.

Sıra:

1. Hemen, paralel: T-043, T-036, T-037. (Bitti, `main`'de.)
2. T-043'ten sonra, paralel: T-023, T-024, T-025, T-044. (T-024, T-025, T-044 bitti, `main`'de; T-023 yarım kaldı, D-44.)
3. İnceleme düzeltmeleri (2026-10-06): önce T-046; sonra paralel T-047 ve T-023'ün devamı. (Bitti, `main` `f201c43`.)
4. Paralel: T-026 (ajan zinciri; dosyası gerçek arayüzlere göre güncellendi) ve T-048 (ajanın son cevabı, Investigation bütçesi, Ariel zaman penceresi). (Bitti, `main` `c301e1e`.)
5. T-049 (zincir düzeltmeleri: Ariel zaman sınırı epoch milisaniye, son cevap, Verification penceresi, QA ve çalışma kaydı, dev skill'leri). (Bitti, `main` `530c01e`.)
6. Paralel (2026-10-06): T-045 (executor'ın akışa bağlanması; dosyası gözden geçirildi), T-050 (aramanın kanıt penceresi, araç açıklamaları), T-051 (Verification'ın bütçe aşımı). (Bitti, `main` `89d17f1`; kararlar T-59–T-61.)
7. Ardından: T-027. Planner dosyasını T-045 birleşince gözden geçirir. Bu üçünün dosyalarını planner, bağımlı oldukları görevler birleşince gerçek arayüzlere göre gözden geçirir; o zamana kadar ajana verilmez.

| Görev | Kapsam | İzinli dizinler | Bağımlı olduğu | Dosya |
|---|---|---|---|---|
| T-043 | Ajan altyapısı: bağlam kanıtı takma adları `ev_c<n>` (T-38), çıktıdaki bütün kanıt alanlarının doğrulanması, öneri AQL'inin AQL Guard'dan geçmesi ve Guard'a `LIMIT`/zaman sırası kuralı (T-39), prompt'un Skill bölümü (T-44), araçsız ajan | `packages/agents/`, `packages/knowledge/` (skill taraması), `packages/policy/` (AQL Guard'ın sıra kuralı) | `main` | [T-043](tasks/T-043-ajan-altyapisi.md) |
| T-023 | Investigation ajanı: `qradar-investigate-read`, Ariel sorguları, timeline, hipotezler, acil event adayları, skill'in talimatı ve gereksinimleri (T-36); lab'da Temporal'sız koşu, AQL'deki çift tırnak ölçümü | `packages/agents/`, `prompts/investigation/`, `config/agents/investigation.yaml`, `tests/e2e/` | T-043 | [T-023](tasks/T-023-investigation-ajani.md) |
| T-024 | Verification ajanı: `soc-verifier`, `qradar-verify-read`; yalnızca yapısal claim'ler ve kanıt; kritik claim'lerin kanıtını yeniden çekme; serbest metni görmeme | `packages/agents/`, `prompts/verification/`, `config/agents/verification.yaml`, `tests/e2e/` | T-043 | [T-024](tasks/T-024-verification-ajani.md) |
| T-025 | Reporting ajanı: `CaseReport`, Türkçe özet (≤400 karakter), adaylardan acil event sıralaması, öneriler; araçsız | `packages/agents/`, `prompts/reporting/`, `config/agents/reporting.yaml` | T-043 | [T-025](tasks/T-025-reporting-ajani.md) |
| T-044 | Orchestrator ajanı ve plan doğrulama (T-41): `CasePlan`, deterministik `validate_plan`, varsayılan plan, plan bütçesi ayarları | `packages/agents/`, `prompts/orchestrator/`, `config/agents/orchestrator.yaml`, `packages/workflows/` (yalnızca `plan` modülü), `packages/activities/` (ayarlar) | T-043 | [T-044](tasks/T-044-orchestrator-plan.md) |
| T-046 | Önceki ajanların metni için `agent.<tür>` kaynakları (T-48): policy paketi, Verification ve Orchestrator | `packages/policy/`, `packages/agents/` (verification, orchestrator ve testleri) | T-024, T-044 | [T-046](tasks/T-046-ajan-metni-kaynaklari.md) |
| T-047 | Reporting düzeltmeleri (T-50): ajan bir kez kurulur, acil event aday numarasıyla seçilir, gerçek kanıt kimliği modele gösterilmez, dev stack testi `tests/e2e/`'ye taşınır | `packages/agents/` (reporting, `RunDeps`), `prompts/reporting/`, `tests/e2e/` | T-025, T-046 | [T-047](tasks/T-047-reporting-duzeltmeleri.md) |
| T-048 | Ajanın son cevabı (bütçe bitmeden araçlar geri çekilir), Investigation ve skill token bütçeleri, investigate profilinden dört listeleme aracı, Ariel penceresinin hazır AQL parçaları, T-54'ün küçük düzeltmeleri (T-52, T-53, T-54) | `packages/agents/`, ajan prompt'ları, `config/agents/investigation.yaml`, `skills/*` (bütçe), `config/connectors/qradar.yaml` (investigate profili), `services/mcp-gateway/tests/`, `packages/activities/` (plan token varsayılanı), `tests/e2e/` | T-023, T-046, T-047 | [T-048](tasks/T-048-ajan-butcesi-ve-zaman-penceresi.md) |
| T-049 | Zincir düzeltmeleri (T-55–T-58): Ariel zaman sınırı epoch milisaniye (Guard dahil), `FinalAnswer` kurulumda ve adım sınırı koşulu, Verification'ın penceresi claim kanıtlarından, migration `0008` (`qa_items.evaluation_no`, `agent_runs.error`), `AIS0C_AGENT_RETRY_MINUTES`, dev'de taslak skill'ler | `packages/agents/` (runner, builder, ajan kurucuları), `packages/policy/` (AQL Guard), ajan prompt'ları, `packages/activities/`, `packages/workflows/` (ad), `packages/storage/`, `packages/knowledge/` (router), `services/worker/`, `deploy/compose/README.md`, `tests/e2e/` | T-026, T-048 | [T-049](tasks/T-049-zincir-duzeltmeleri.md) |
| T-050 | Gateway sayısal START/STOP'lu aramanın kanıtına sorgunun kesin penceresini yazar; Investigation profilindeki `create_ariel_search` ve `list_offenses` açıklamalarına doğru örnekler (`QIDNAME(qid)`, QID ≠ Windows event ID, `list_offenses` filtreleri) | `services/mcp-gateway/`, `config/connectors/qradar.yaml` (açıklamalar), `tests/e2e/` (ölçüm) | T-049 | [T-050](tasks/T-050-kanit-penceresi-arac-aciklamalari.md) |
| T-051 | Verification'ın bütçe aşımı (offense 34: 82.377 > 80.000 token, sonuçsuz): son cevap eşiğinin düzeltilmesi, gerekirse Verification bütçesi | `packages/agents/` (runner), `config/agents/verification.yaml` (bütçe), `packages/activities/` (plan varsayılanı), `tests/e2e/` (ölçüm) | T-049 | [T-051](tasks/T-051-verification-butcesi.md) |
| T-026 | `CaseWorkflow`'un ajan zinciri: ajan child workflow'ları, skill doğrulaması ve kaydı, karar ve bildirim seviyesi (T-42), QA kuralları (D-35), rapor/acil event/öneri kaydı; `run_id`'nin açık geçirilmesi (T-29); ertelenmiş değerlendirme ve `no_ai_decision` aralığı (T-30) | `packages/workflows/`, `packages/activities/`, `packages/storage/`, `services/worker/`, `tests/e2e/` | T-023, T-024, T-025, T-044, T-046, T-047 | [T-026](tasks/T-026-vaka-ajan-zinciri.md) |
| T-045 | Executor'ın vaka akışına bağlanması: executor worker'ı ve `soc-executor` kuyruğu (T-33 (1)), run marker (T-33 (3)), not ve e-posta çağrıları, shadow modunda `disabled` kayıtlar | `packages/workflows/`, `packages/activities/`, `services/worker/` | T-026 | [T-045](tasks/T-045-executor-vaka-akisi.md) |
| T-027 | Grup değerlendirmesi ve gruplama kaçışları (T-22, T-46); grup notu ve grup e-postası; bekleyen ve gruplanmış offense'lerin katalog kontrolü (T-30 (3)) | `packages/activities/`, `packages/workflows/`, `packages/storage/`, `services/worker/` | T-026, T-045, T-036 | [T-027](tasks/T-027-grup-degerlendirmesi.md) |
| T-036 | Bildirim grupları ve yönlendirme tablosu (D-41, T-43), grup seviyesi yükselince yeni grup e-postası (D-42), not ve e-postada ortak Türkçe etiketler, executor'ın ortak parçalarının `common`'a taşınması (T-33 (5)) | `packages/executor/`, `packages/storage/`, `packages/activities/` | T-041 | [T-036](tasks/T-036-bildirim-gruplari.md) |
| T-037 | T-022'nin bağlanması: batch worker (`python -m ais0c_worker batch`), `knowledge-sync` Schedule'ının açılışta kurulması, dev'de envanter token'ı | `services/worker/`, `deploy/compose/README.md` | — | [T-037](tasks/T-037-knowledge-sync-worker.md) |

## Faz 1, dalga C

| Görev | Kapsam | Bağımlı olduğu |
|---|---|---|
| T-028 | API: vakalar, adımlar, geri bildirim, QA, gruplar, katalog (storage'da `qradar_enabled` ve `missing` filtreleri dahil, T-37), kritik varlıklar, alıcılar ve yönlendirme (T-036'nın repository'leri), SLA metrikleri, platform bayrakları, `/me`, `/health` ([api.md](api.md)); geliştirme için basit kimlik doğrulama modu. Dosya: [T-028](tasks/T-028-api.md) (T-63) | T-022, T-026, T-036 |
| T-029 | Arayüz MVP: kuyruk, vaka detayı, geri bildirim, QA, gruplar, katalog, kill switch | T-028 |
| T-030 | Harness Faz 1: golden suite koşucusu, `pass^k`, adversarial FN, güven katmanları ve skill suite'leri, on-prem modellerle model geçiş gate'i | T-026 |
| T-031 | Prod shadow dağıtımı: prod compose, LiteLLM prod konfigürasyonu, shadow modu, case, batch ve executor worker'larının compose servisleri (T-33, T-037, T-045), dağıtım notları | T-018, T-026, T-037, T-045, H-7 |

## Canary öncesi

| Görev | Kapsam | Bağımlı olduğu |
|---|---|---|
| T-032 | Asgari sağlık alarmları: intake durdu, log source sustu, not/e-posta hataları (yalnızca `failed`, e-postada `rejected` de; `disabled` hiçbir zaman hata sayılmaz, T-37); `soc-executor` kuyruğunda worker yok ve bırakılan executor çağrısının `failed` kaydı (`executor_unavailable`, T-59 (7)); e-posta ve QRadar'a syslog (T-23) | T-017, T-020, T-022, T-045 |
| T-033 | Çift kontrol: `change_approvals` akışı, API ve arayüz (D-36) | T-028, T-029 |
| T-034 | AI olay müdahale playbook'ları: `docs/ai-incident-response.md` (D-37) | — |
| T-035 | OIDC entegrasyonu ve audit saklama | H-6 |

## Takvim önerisi

| Dönem | Hedef |
|---|---|
| Ekim 2026 | H-1–H-3, T-013, T-014 |
| Kasım 2026 | Dalga A ve dalga B |
| Aralık 2026 | Dalga C; ay sonunda prod shadow |
| Aralık 2026 – Ocak 2027 | Canary öncesi görevler; ardından canary (architecture §28) |
