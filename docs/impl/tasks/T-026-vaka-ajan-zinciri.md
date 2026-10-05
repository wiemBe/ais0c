# T-026: CaseWorkflow'un ajan zinciri

> Bu görev T-023, T-024, T-025 ve T-044 birleştikten sonra verilir. Planner o zaman dosyayı bu görevlerin gerçek arayüzlerine göre gözden geçirir.

## Amaç

Bir değerlendirmeyi Triage'dan Reporting'e kadar çalıştırmak:

1. Triage karar verir.
2. Orchestrator'ın planı doğrulanır (T-044).
3. Planın ajanları (Investigation, Verification) child workflow olarak koşar.
4. Reporting raporu yazar.

Karar, bildirim seviyesi, zorunlu kontrol (QA) kayıtları, rapor, acil event'ler ve öneriler kaydedilir. Ayrıca:

- ajan çalışmalarının run ID'si workflow'dan açıkça verilir (T-29);
- bastırılan güncellemenin ertelenmiş değerlendirmesi ve `no_ai_decision` vakanın aralığı (T-30 (1), (2)).

Not ve e-posta bu görevde yoktur (T-045).

## Okunacaklar

- `docs/architecture.md` §6, §7, §9 (bütün alt bölümler), §20, §21
- `docs/impl/contracts.md`: `AgentTask`, `CasePlan`, `TriageResult`, `InvestigationResult`, `VerificationResult`, `CaseReport`, `UrgentEvent`, `Recommendation`, `SkillRef`
- `docs/impl/data-model.md`: `cases`, `qa_items`, `urgent_events`, `recommendations`, `agent_runs`
- `docs/decisions.md`: D-30, D-31, D-33, D-35, T-21, T-29, T-30, T-40, T-41, T-42, T-45
- T-023, T-024, T-025, T-043 ve T-044'ün PR'ları

## İzinli dizinler

- `packages/workflows/`
- `packages/activities/`
- `packages/storage/`: QA, acil event ve öneri repository'leri; `start_agent_run`'a `skill`
- `services/worker/`, `services/worker/tests/`
- `tests/e2e/`: lab e2e testinin zincire göre güncellenmesi

Bu dizinlerin dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

- `AgentTask`, `CasePlan`, `PlanStep`, `TriageResult`, `InvestigationResult`, `VerificationResult`, `CaseReport`, `UrgentEvent`, `Recommendation`, `Claim`, `EvidenceRef`, `SkillRef`, `QAReason`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir. Workflow testleri Temporal test ortamında, sahte ajanlarla ve zaman atlatmayla koşar.

1. **Ajan child workflow'u.**
   - Her ajan, TriageWorkflow'daki düzenle bir child workflow'da koşar: kayıt activity'leri, TemporalDurability, duvar saati bütçesi ve başarısızlık sınıflaması.
   - Run ID'leri `<case_id>-<ajan>-<değerlendirme no>` biçimindedir. Model kesintisinden sonraki yeniden deneme `-retry` ekini alır. ID'ler `RunId` desenine uyar.
   - Başlangıç kaydı `agent_runs`'a ajanın model release'ini ve kullanılan skill'i (`SkillRef`) yazar; storage'ın `start_agent_run`'ı `skill` parametresi kazanır.
   - Triage bu düzene taşınabilir. Taşınırsa davranışı ve mevcut testleri değişmez.
2. **Run ID (T-29).** Ajan runtime'ları run ID'yi workflow'dan argüman olarak alır, `workflow.info()`'dan okumaz. Test: verilen ID gateway'e giden her `ToolIntent`'te bulunur.
3. **Zincir.** Sıra şöyledir:
   - Triage çalışır.
   - Triage karar vermezse mevcut davranış sürer (`no_ai_decision`, D-33'ün yeniden denemesi).
   - Aday skill'ler bir activity'de router'la çıkarılır (`candidate_skills`, her plan ajanının rolü için).
   - Orchestrator çalışır ve planı `validate_plan` ile doğrulanır.
   - Adımlar sırayla koşar.
   - Reporting çalışır.

   Bir halka sonuç vermezse:
   - Orchestrator: varsayılan plan kullanılır.
   - Investigation: karar Triage'ınki kalır.
   - Verification: QA (T-42).
   - Reporting: karar raporsuz kaydedilir.

   Test: her halkanın başarısızlığı.
4. **Girdiler (T-45).** Her ajanın girdisi T-45'teki gibi kurulur. Kanıt kayıtları storage'dan `EvidenceRef` olarak bir activity'de okunur. Serbest metin alanları bir sonraki ajana taşınmaz.
   - Kritik claim'ler: karar FP ise veya bildirim seviyesi high/critical ise, kararın bütün claim'leri kritiktir.
   - Test: sahte ajanların aldığı girdiler beklenen alanları taşır ve `rationale` taşımaz.
5. **Skill doğrulaması (T-21).** Planda seçilen skill'in rolü, durumu (prod'da `approved`), sürümü, son kullanma tarihi ve içerik hash'i, ajan başlamadan bir activity'de kontrol edilir. Geçmeyen skill'le adım skill'siz koşar ve neden kaydedilir. Test.
6. **Karar ve seviye (T-42).**
   - Karar ve bildirim seviyesi T-42'ye göre belirlenir.
   - `record_decision` şunları kaydeder: kararı, raporu (`CaseReport`, varsa), acil event'leri ve önerileri (`urgent_events`, `recommendations`, değerlendirme numarasıyla).
   - Aynı değerlendirmenin tekrarı satırları çoğaltmaz.
7. **Zorunlu kontrol (architecture §9, D-35, T-42).** Şu durumlar ayrı `qa_items` satırı açar:
   - `verifier_conflict`;
   - `injection_suspected` (herhangi bir ajanın çıktısında);
   - `low_confidence`;
   - `fp_with_data_gap`;
   - `random_sample` (deterministik; oranlar `CaseSettings`'te, varsayılan %10 ve %30).

   Aynı değerlendirme için bir neden bir kez yazılır. Test: her neden ve örneklemin tanımsız kural oranı.
8. **SLA.** SLA bütün zinciri kapsar.
   - Süre dolunca vaka `no_ai_decision` olur ve zincir sürer; geç gelen karar bu durumun yerine geçer (D-30).
   - Vaka kapanırsa zincir bırakılır (ABANDON). Bu, Triage'ın bugünkü düzenidir.
9. **Ertelenmiş değerlendirme (T-30 (1)).** Yalnızca event sayısı arttığı için aralık içinde bastırılan bir güncelleme, aralık bitiminde tek bir ertelenmiş değerlendirme planlar. Arada değerlendirme olursa plan düşer. Test (zaman atlatma).
10. **Kararsız vakanın aralığı (T-30 (2)).** `no_ai_decision` durumundaki vakada yeniden değerlendirme aralığı 30 dakika değil, retry bekleme süresidir (varsayılan 5 dakika). Test.
11. **Worker.**
    - Case worker yeni ajanları kurar: manifest'ler, prompt'lar ve profil token'ları (`gateway-token-qradar-investigate-read`, `gateway-token-qradar-verify-read`). Skill'ler `skills/`'ten yüklenir: dev'de taslaklarla, prod'da onaylılarla.
    - Bütün activity'ler ve workflow'lar kaydedilir; iki `names.py` listesi eşleşir (`test_names.py`).
    - Eksik bir manifest, token veya skill hatası başlangıçta açık hatayla durur.
12. **Lab e2e.** `tests/e2e/test_lab_triage.py`, zincirin kayıtlarını da doğrulayacak şekilde güncellenir:
    - her ajanın çalışması;
    - `cases.report`;
    - acil event'ler;
    - QA satırları (varsa).

    Lab koşusunu planner yapar; kodlama ajanı lab'da offense açmaz ve kapatmaz.

## Kapsam dışı

- Not ve e-posta, executor worker'ı (T-045)
- Grup değerlendirmesi (T-027)
- API ve arayüz (T-028, T-029)

## Bağımlılıklar

- T-023, T-024, T-025, T-044 (dolayısıyla T-043)
- T-041 (`main`'de)

## Notlar

- Workflow kodu deterministiktir: ağ, dosya, saat ve rastgelelik yoktur. Örneklem T-42'deki hash ile seçilir.
- Ajanların activity ayarları (model 3 dk ve 4 deneme, araç 200 sn ve 3 deneme, heartbeat, ABANDON) Triage'dakiyle aynı başlar. Değişiklik gerekirse gerekçesi PR'a yazılır.
- Plan ajanlarının gateway token'ları dev stack'te `deploy/compose/secrets/agents/` altında vardır (T-018).
- Bu görev büyükse ajan, PR'da hangi kriterleri hangi commit'te bitirdiğini yazar. Görev yine tek PR'dır.
