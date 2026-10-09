# Görev Pipeline'ı: Faz 0 kapanışı, Faz 1 ve canary öncesi

> Son güncelleme: 2026-10-07 (T-030'un dosyası, harness'in bölünmesi T-64; T-031'in model geçiş gate'ine bağlanması). Önceki: 2026-10-05 (T-28–T-37 kararları; T-040–T-042 bitti; H-3 yapıldı, H-2 ertelendi; dalga B görev dosyaları ve T-38–T-46; H-7). Faz 0 görevleri (T-001–T-012) [multi-agent-dev.md](multi-agent-dev.md)'dedir. Bu pipeline, 2026-10-03 kararlarını (D-29–D-38, T-18–T-25) ve `future.md`'den alınan maddeleri koda taşır.

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
| H-8 | Prod'da QRadar'a sağlık alarmları için bir syslog log source'u (kaynak: platformun batch worker'ı) ve `ais0c` alarmlarını yakalayan bir kural; `AIS0C_ALARM_SYSLOG_HOST`/`PORT`/`PROTOCOL` değerleri (T-68, T-72) | Canary öncesi |
| H-9 | Prod'da ilk KnowledgeSync'ten sonra log source'ların sınıflandırılması: telemetri raporu (etkin log source'lar, tip başına sayı ve sınıf; devre dışı ve kalkmış olanlar hariç), sınıfsız tiplerin ve Universal DSM log source'larının admin tarafından atanması (Brightmail, OPSWAT, özel tipler; çift kontrol). CSV repoya girmez (T-95). | Shadow'un ilk günü (T-031) |

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

Sıra (2026-10-07):

1. Bitti, `main`'de: T-027–T-030, T-032 (kararlar T-65–T-73); T-052, T-053, T-054 (T-74–T-76; T-054'ün gate'i geçmedi); T-033, T-055, T-056, T-058, T-059 (T-79–T-83; Triage'ın güvenlik gate'i T-056'nın koşularında geçti, T-058'in lab kuralları planner tarafından kuruldu).
2. Bitti, `main`'de (2026-10-07 gece): T-061, T-062 (T-86, T-87). T-063 de `main`'de (T-91; güvenlik gate'leri geçti). T-064'ün ilk partisi (60 taslak skill) 2026-10-08'de `main`'de (T-92).
3. **Bitti, `main`'de (2026-10-08):** T-065, T-068 (T-96, T-97); T-057, T-069, T-070 (T-99–T-101); T-072 (T-102; ölçümü sağlayıcı yüzünden geçersiz).
4. **2026-10-09:** T-073 (T-105), T-071 (T-106, Codex) ve T-074 (T-107) `main`'de. T-075 (T-108) ve T-067 (T-109) `main`'de. **MVP (prod shadow) yolu (T-110):** T-076 ve T-078 paralel, sonra T-077; runbook planner'da. OpenRouter kredisi bitti (T-104); gerçek model koşuları T-074'ün ücretsiz config'iyle, yalnızca akış doğrulaması olarak. Önceki plan: T-071 (skill bölümünde çözüm; workflow değişikliği, yüksek effort; birleşince açık CaseWorkflow'lar sonlandırılır) ve T-073 (dev'de sağlayıcı sabitleme, bütçeyi aşan grubun kırpılması, dcsync ölçümü). Gerçek model ölçümleri T-073 birleşmeden yapılmaz.
5. Planner: senaryo setinin lab e2e koşuları (`AIS0C_E2E_SCENARIO`) ve kayıtları; sonra T-060'ın dosyası. T-060'tan sonra T-066 (Investigation/Verification v3). T-057, T-065 ve T-070'ten sonra T-067 (aday özeti).
6. Ardından T-031 (H-7, S-12 ve on-prem gate'inden sonra), T-035 (H-6).

| Görev | Kapsam | Bağımlı olduğu |
|---|---|---|
| T-028 | API: vakalar, adımlar, geri bildirim, QA, gruplar, katalog (storage'da `qradar_enabled` ve `missing` filtreleri dahil, T-37), kritik varlıklar, alıcılar ve yönlendirme (T-036'nın repository'leri), SLA metrikleri, platform bayrakları, `/me`, `/health` ([api.md](api.md)); geliştirme için basit kimlik doğrulama modu. Dosya: [T-028](tasks/T-028-api.md) (T-63) | T-022, T-026, T-036 |
| T-029 | Arayüz MVP: kuyruk, vaka detayı, geri bildirim, QA, gruplar, katalog, kill switch, kritik varlıklar, alıcılar ve yönlendirme, SLA; API'ye QRadar offense linki ve grup özeti (T-65 (7), T-66 (2)); CI'da frontend işi. Dosya: [T-029](tasks/T-029-arayuz-mvp.md) (T-69) | T-028 |
| T-030 | Harness koşucusu (`fixture` modu, k koşu, `pass^k`, deterministik değerlendiriciler, run envelope'lu rapor, hard gate tablosu), Triage adaptörü, Trust Layers ve yeni Adversarial FN suite'leri, model geçiş gate'inin karşılaştırma komutu (B2), değişen model sürümlerinin listesi. Dosya: [T-030](tasks/T-030-harness-kosucusu.md) (T-64) | T-026 |
| T-052 | Harness replay'i: lab kaydı ve anonimleştirme, kayıtlı event tablosu üzerinde AQL alt kümesi, Ariel yaşam döngüsü, Investigation ve Verification adaptörleri, `investigation-gold` ve `verification-gold`, T-67'nin harness maddeleri (gateway kontrollerinin açık API'si, türetilmiş cevaplar, yazım hatalı araç adı, koşu dosyaları). Dosya: [T-052](tasks/T-052-harness-replay.md) (T-70) | T-030 |
| T-053 | Harness: Orchestrator ve Reporting adaptörleri, `orchestrator-gold`, `reporting-gold`, Turkish Quality suite'i ve LLM değerlendiricisi (`soc-reasoning`), registry'deki `turkish_quality` (D-44), Orchestrator'ın gerekçesiz `injection_suspected`'ının ölçümü. Dosya: [T-053](tasks/T-053-harness-orchestrator-reporting.md) (T-71) | T-030 |
| T-054 | Triage prompt v3: katalog notu ve varlık açıklaması kararı veremez, untrusted veride karar iddiası talimattır, cevapsız araç zararsızlık kanıtı değildir, `rationale` ≤ 600, grup vakası bölümü; manifest `1.2.0` ve `adversarial-fn`; grup vakasının kararsız notu (T-65 (2)); güvenlik suite'lerinin iki gerçek koşusu. Dosya: [T-054](tasks/T-054-triage-prompt-v3.md) | T-027, T-030 |
| T-055 | Replay'in eksikleri (`list_assets` türetilir, kayıttaki offense açık görünür, tek anonimleştirici), kayıt katmanları, `windows-dcsync` skill suite'i (tespit, eksik telemetri, payload'da talimat), Investigation/Verification/plan/skill bütçelerinin ölçümü, ajan başına `budget_exhausted` oranı, T-60'ın sorusu (T-75, T-76). Dosya: [T-055](tasks/T-055-skill-suite-ve-butceler.md) | T-052 |
| T-057 | Orchestrator prompt v2 (`injection_suspected` yalnızca Orchestrator'a talimat vermeye çalışan metin için) ve Reporting prompt v2 (data gap'ler, girdiden aynen zaman, terim tutarlılığı); Turkish Quality'nin geçme kuralı senaryo başına; değerlendirici gerekçeleri koşu dosyasında (T-75). Dosya: [T-057](tasks/T-057-orchestrator-reporting-prompt-v2.md) | T-053 |
| T-058 | Lab senaryo seti (T-78): Kerberoasting, password spraying, WAF (F5 ASM) SQLi/XSS geçti, tarama engellendi, onaylı tarayıcı; log üreticinin yeni türleri, lab kurallarının kaynağı ve eklenti zip'i, e2e'de senaryo seçimi. Dosya: [T-058](tasks/T-058-lab-senaryo-seti.md) | — |
| T-059 | Triage Gold suite'i (`fixture` modu, T-78'in sekiz senaryosu), kalite senaryolarında saldırı alanlarının isteğe bağlı olması, karar ve seviye doğruluğu metrikleri. Dosya: [T-059](tasks/T-059-triage-gold.md) | T-030 |
| T-060 | Senaryo setinin lab kayıtları ve replay suite'leri (Investigation, Verification), `vpn-new-country` ve `password-spraying` skill suite'leri. Dosyası kurallar kurulup senaryolar lab'da koşulunca yazılır. | T-055, T-058, kuralların kurulması |
| T-061 | Lab kurallarının zip'i QRadar'ın içerik export biçiminde (`<custom_rule>`, base64 `rule_data`, CRE test sınıfları, `manifest.txt`); kaynaklar lab'da kurulu kurallarla eşit, kimlikler lab'dakilerle aynı (T-82). Dosya: [T-061](tasks/T-061-lab-kural-zipi.md) | T-058 |
| T-062 | Bütçeler (T-85): token kaçak koruması (Investigation ve skill 600.000, Verification 250.000, plan 900.000), süre (Investigation ve skill 360 s, plan 600 s); v2 prompt'larla ekonomik ölçüm (kalite k = 3, skill suite'i k = 5), süre bütçelerinin kesinleşmesi, skill suite'inin gate'i, lab testlerinde çıktı düzeltme sayısı. Dosya: [T-062](tasks/T-062-butceler-v2-olcum.md) | T-055, T-056 |
| T-063 | Triage prompt v4: engelleme kararı değil seviyeyi belirler (engellenmiş saldırı `tp`/low, uygulamaya ulaşan `tp`/high), zararsız bağlam okunmadan yokluk iddiası yok; s8 ve `tg-05`'in beklentisi `tp`; Gold'un `cited_tools`'u; v3/v4 Gold ve güvenlik suite'leri ölçümü (T-83, T-84); yetki yalnızca güvenilir olgudan, adres bloğu kanıt değil (T-88). Dosya: [T-063](tasks/T-063-triage-prompt-v4.md) | T-059 |
| T-064 | Skill kataloğu ilk parti (T-90): `skills/CATALOG.md` (internal/external), on bir yeni taslak skill (Kerberoasting, brute force, yanal hareket, ayrıcalıklı grup, log silme; web SQLi, XSS, tarama, path traversal, komut enjeksiyonu; VPN brute force), içerik testleri; rehber [skill-authoring.md](skill-authoring.md). Bitti (T-92): 60 taslak. Dosya: [T-064](tasks/T-064-skill-katalogu.md) | T-062 |
| T-065 | Skill içeriğinin incelemesi ve düzeltmeleri (T-92): kapsam boşluğu `fp` değil (golden/silver ticket), eski üç taslak rehbere, T-064'ün içerik testleri, `fp` maddeleri, katalog notları, 60 skill'in inceleme tablosu. Telemetriye dokunmaz (T-070). Hiçbir skill silinmez. Dosya: [T-065](tasks/T-065-skill-katalogu-duzeltmeleri.md) | T-064 |
| T-066 | Investigation ve Verification prompt v3: engelleme seviyeyi belirler, yetki yalnızca `org_context`'ten, yokluk iddiası araçtan (T-84, T-88, Triage v4'ün kuralları); WAF ve sahte yetki senaryoları, k = 3/5 ölçüm. Dosya: [T-066](tasks/T-066-investigation-verification-prompt-v3.md) | T-060 |
| T-067 | Skill manifest'inde `summary`, Orchestrator adayı özetiyle görür; aynı tekniği paylaşan skill'ler arasında seçim senaryoları (T-94). Dosya: [T-067](tasks/T-067-skill-aday-ozeti.md) | T-057, T-065, T-070 |
| T-068 | Telemetri sınıfları 1/4 (T-95): `TelemetryClass` enum'u, migration `0012` (`qradar_enabled`, `default_telemetry_classes`, `telemetry_classes`), `config/telemetry/log-source-classes.yaml`, KnowledgeSync'te `enabled` ve varsayılan sınıflar, `catalog telemetry` CLI'ı. Dosya: [T-068](tasks/T-068-telemetri-siniflari.md) | T-064 |
| T-069 | Telemetri sınıfları 2/4: `/catalog/log-sources`'ta sınıf alanları ve filtreleri, `PUT` ile atama (çift kontrol; alan yoksa değişmez), OpenAPI ve `schema.d.ts`. Dosya: [T-069](tasks/T-069-telemetri-siniflari-api.md) | T-068 |
| T-070 | Telemetri sınıfları 3/4: skill manifest'inde `telemetry_class` (`siem-internal` yasak), 60 `skill.yaml`'ın çevrilmesi, Entra'daki posta kutusu maddeleri, katalogdaki telemetri sütunu. Dosya: [T-070](tasks/T-070-skill-telemetri-sinifi.md) | T-068 |
| T-071 | Telemetri sınıfları 4/4: `skill_telemetry` activity'si, `InvestigationInput.telemetry`, skill bölümünde kurulumun tipleri ve log source kimlikleri ya da "no enabled log source" satırı. Dosya: [T-071](tasks/T-071-skill-telemetrisinin-cozulmesi.md) | T-068, T-070 |
| T-072 | Son cevap kuralında paralel araç çağrısı payı (araçlar en az 2 çağrı sığarken geri çekilir; T-065'in dcsync koşusunda 2 koşu 23/24'te iki çağrıyla cevapsız düştü), harness hatasının traceback'i, dcsync suite'i k = 5. Dosya: [T-072](tasks/T-072-paralel-arac-cagrisi-payi.md) | T-065 |
| T-073 | Dev'de OpenRouter sağlayıcısı alias başına sabit (`order`, `allow_fallbacks: false`; T-103), sağlayıcı release'e girer; `FinalAnswer` bütçeyi aşan araç grubunu `after_model_request`'te kırpar (T-98); dcsync suite'i k = 5. Dosya: [T-073](tasks/T-073-dev-saglayici-ve-grup-kirpma.md) | T-072 |
| T-074 | Dev'de ücretsiz model yedeği: ikinci LiteLLM config'i (`litellm.dev-free.yaml`) ve registry'si, dört alias OpenCode Zen'in `space-bunny-free`'sine; compose'da `AIS0C_LITELLM_CONFIG`, e2e'de `AIS0C_E2E_MODEL_REGISTRY` (T-104). Dosya: [T-074](tasks/T-074-dev-ucretsiz-model.md) | T-073 |
| T-075 | Lab e2e'si log üretecine seçilen senaryoyu vermiyor (`--scenario` değersiz, T-058'den beri); komut `loggen_command`'a ayrılır, lab gerektirmeyen testler. Dosya: [T-075](tasks/T-075-e2e-senaryo-argumani.md) | T-058 |
| T-076 | MVP 1/3: platform imajları (`ais0c-platform`: worker'lar ve API, onaylı içerik `config/agents`, `config/models`, `prompts`, `skills` imajda; `ais0c-ui`: nginx, yalnızca TLS 8443, `/api/` → `api:8000`). Dosya: [T-076](tasks/T-076-platform-imajlari.md) | — |
| T-078 | MVP 2/3: `ais0c_worker migrate` (`--check`) ve `ais0c_worker preflight` (veritabanı, kill switch kapalı, prod skill modu, onaylı skill, H-7 `verify`, gateway profilleri, Temporal, modeller). Dosya: [T-078](tasks/T-078-migrate-ve-preflight.md) | — |
| T-077 | MVP 3/3: prod shadow compose (`docker-compose.prod.yaml`, `.env.prod.example`): imajdan servisler, tek seferlik `migrate`, sabit prod skill modu ve registry, yalnızca UI portu dışarıda, sırlar dosyadan. Dosya: [T-077](tasks/T-077-prod-compose.md) | T-076, T-078 |
| T-056 | Son cevabın düzeltme payı: araçlar geri çekilirken bir çıktı düzeltme isteğine de yer kalır (offense 36: Verification'ın `reason`'ı 300 karakteri aştı, düzeltme isteği 120.000'i aştı); Verification ve Investigation prompt v2'de alan sınırları; lab ölçümü. Dosya: [T-056](tasks/T-056-cevap-duzeltme-payi.md) | T-051 |
| T-031 | Prod shadow dağıtımı: prod compose, LiteLLM prod konfigürasyonu, shadow modu, case, batch ve executor worker'larının compose servisleri (T-33, T-037, T-045), dağıtım notları. Shadow başlamadan model geçiş gate'i on-prem prod modelleriyle koşar ve geçer: dev raporu baseline, on-prem raporu candidate (T-030'un `gate` komutu, T-64) | T-018, T-026, T-030, T-037, T-045, H-7 |

## Canary öncesi

| Görev | Kapsam | Bağımlı olduğu |
|---|---|---|
| T-032 | Asgari sağlık alarmları: intake durdu, log source sustu, not/e-posta hataları (yalnızca `failed`, e-postada `rejected` de; `disabled` hiçbir zaman hata sayılmaz, T-37); `soc-executor` kuyruğunda worker yok ve bırakılan executor çağrısının `failed` kaydı (`executor_unavailable`, T-59 (7)); e-posta ve QRadar'a syslog (T-23) Dosya: [T-032](tasks/T-032-saglik-alarmlari.md) (T-68) | T-017, T-020, T-022, T-045, T-027 |
| T-033 | Çift kontrol: `change_approvals` (migration `0011`), katalog/kritik varlık değişiklikleri ve kill switch'in açılması onay bekler, kapatma tek adım; API (`/changes`) ve arayüz ("Bekleyen değişiklikler") (D-36, T-77). Dosya: [T-033](tasks/T-033-cift-kontrol.md) | T-028, T-029 |
| T-034 | AI olay müdahale playbook'ları: `docs/ai-incident-response.md` (D-37) | — |
| T-035 | OIDC entegrasyonu ve audit saklama | H-6 |

## Takvim önerisi

| Dönem | Hedef |
|---|---|
| Ekim 2026 | H-1–H-3, T-013, T-014 |
| Kasım 2026 | Dalga A ve dalga B |
| Aralık 2026 | Dalga C; ay sonunda prod shadow |
| Aralık 2026 – Ocak 2027 | Canary öncesi görevler; ardından canary (architecture §28) |
