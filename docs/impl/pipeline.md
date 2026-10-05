# Görev Pipeline'ı: Faz 0 kapanışı, Faz 1 ve canary öncesi

> Son güncelleme: 2026-10-05 (T-28–T-37 kararları; T-040–T-042 bitti; H-3 yapıldı, H-2 ertelendi; H-7). Faz 0 görevleri (T-001–T-012) [multi-agent-dev.md](multi-agent-dev.md)'dedir. Bu pipeline, 2026-10-03 kararlarını (D-29–D-38, T-18–T-25) ve `future.md`'den alınan maddeleri koda taşır.

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

| Görev | Kapsam | Bağımlı olduğu |
|---|---|---|
| T-023 | Investigation ajanı: `qradar-investigate-read`, AQL Guard'dan geçen sorgular, timeline, hipotezler, acil event adayları, skill talimatının, `required_telemetry` ve `required_evidence`'ın prompt'a eklenmesi (T-36) | T-015, T-021 |
| T-024 | Verification ajanı: `soc-verifier`, `qradar-verify-read`; yalnızca yapısal claim'ler; kritik bulgularda kanıtı yeniden çekme; uyuşmazlıkta QA | T-015 |
| T-025 | Reporting ajanı: `CaseReport`, Türkçe özet, acil event sıralaması, `NoteContent` | T-015 |
| T-026 | Orchestrator ve `CaseWorkflow`'un genişletilmesi: skill seçimli `CasePlan`, plan doğrulama, child ajanlar, bildirim seviyesi formülü, QA kuralları (D-35), executor çağrıları, shadow modu (yazma kapalı). Ayrıca: `run_id`'nin workflow'dan açık geçirilmesi (T-29); bastırılan güncellemenin ertelenmiş değerlendirmesi ve `no_ai_decision` aralığı (T-30); executor'ın ayrı worker'ı ve `soc-executor` kuyruğu, not run marker'ı (T-33) | T-019, T-020, T-021, T-023, T-024, T-025, T-041 |
| T-027 | Grup değerlendirmesi ve gruplama kaçışları (T-22) | T-026 |
| T-036 | Bildirim grupları ve yönlendirme tablosu (D-41); grup seviyesi yükselince yeni grup e-postası (D-42); not ve e-postada ortak Türkçe etiketler ve executor'ın ortak parçalarının `common`'a taşınması (T-33) | T-020, T-041 |
| T-037 | T-022'nin bağlanması: batch worker, `knowledge-sync` Schedule'ının açılışta kurulması, compose'da inventory token'ı | T-022 |

## Faz 1, dalga C

| Görev | Kapsam | Bağımlı olduğu |
|---|---|---|
| T-028 | API: vakalar, adımlar, geri bildirim, QA, gruplar, katalog (storage'da `qradar_enabled` ve `missing` filtreleri dahil, T-37), kritik varlıklar, alıcılar, SLA metrikleri, platform bayrakları, `/me`, `/health` ([api.md](api.md)); geliştirme için basit kimlik doğrulama modu | T-022, T-026 |
| T-029 | Arayüz MVP: kuyruk, vaka detayı, geri bildirim, QA, gruplar, katalog, kill switch | T-028 |
| T-030 | Harness Faz 1: golden suite koşucusu, `pass^k`, adversarial FN, güven katmanları ve skill suite'leri, on-prem modellerle model geçiş gate'i | T-026 |
| T-031 | Prod shadow dağıtımı: prod compose, LiteLLM prod konfigürasyonu, shadow modu, executor worker'ının compose servisi (T-33), dağıtım notları | T-018, T-026, H-7 |

## Canary öncesi

| Görev | Kapsam | Bağımlı olduğu |
|---|---|---|
| T-032 | Asgari sağlık alarmları: intake durdu, log source sustu, not/e-posta hataları (yalnızca `failed`, e-postada `rejected` de; `disabled` hiçbir zaman hata sayılmaz, T-37); e-posta ve QRadar'a syslog (T-23) | T-017, T-020, T-022 |
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
