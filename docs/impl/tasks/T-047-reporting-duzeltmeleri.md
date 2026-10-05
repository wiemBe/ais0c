# T-047: Reporting düzeltmeleri

## Amaç

T-025'in Reporting ajanını T-026'nın workflow'una bağlanabilir ve güvenilir hale getirmek (T-50). Üç sorun var:

1. `ReportingAgent.bind` her koşuda `create_agent` çağırıyor. `TemporalDurability` ajanın worker açılışında bir kez kurulmasını ister; her koşuda kurulan ajan workflow'da çalışmaz.
2. Model acil event'in dokuz tanımlayıcısını adaydan birebir kopyalıyor. Bunların içinde 32 karakterlik gerçek `evidence_id` ve 2000 karaktere kadar AQL var. Gerçek kimlik modele gösteriliyor; T-27 tam olarak bu kopyalama hatası yüzünden alındı.
3. `packages/agents/tests/test_reporting_dev_stack.py` hiç koşamıyor: `packages/agents/tests/conftest.py` gerçek model isteklerini kapatıyor.

## Okunacaklar

- `docs/decisions.md`: T-27, T-38, T-45, T-48, T-50, D-44
- `docs/impl/tasks/T-025-reporting-ajani.md` ve `../ais0c-prs/PR-T-025.md`
- `packages/agents/src/ais0c_agents/verification.py`: ajan bir kez kurulur, koşuya özgü veri `RunDeps.reviewed_claim_texts`'te (örnek kalıp)
- `docs/impl/prompts.md` "Güvenilmez veri"

## Branch

`agent/<araç>/T-047`, `main`'den, T-046 birleştikten sonra, ayrı bir worktree'de. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `packages/agents/src/ais0c_agents/reporting.py`, `packages/agents/src/ais0c_agents/toolset.py` (yalnızca `RunDeps`'e alan eklemek), `packages/agents/src/ais0c_agents/__init__.py`
- `packages/agents/tests/test_reporting.py`, `packages/agents/tests/test_reporting_dev_stack.py` (silinir)
- `prompts/reporting/v1.md` (v1 hiç yayınlanmadı; yerinde değişir, manifest sürümü 1.0.0 kalır)
- `tests/e2e/test_dev_reporting.py` (yeni)

Bu dosyaların dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

`CaseReport`, `UrgentEvent`, `Recommendation`, `DataGap`, `Claim`, `EvidenceRef`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Bir kez kurulum.** `build_reporting_agent` Pydantic AI ajanını bir kez kurar; `run` `create_agent` çağırmaz ve her koşuda aynı ajanı kullanır. Koşuya özgü doğrulayıcı verisi (aday sayısı, kanıt alıntıları) yeni `RunDeps` alanlarındadır. Alanların varsayılanı boştur, böylece eski Temporal payload'ları geçerli kalır. Model `RunDeps`'i görmez.
   - Test: iki farklı görevle iki koşu aynı ajan nesnesini kullanır ve her biri kendi adaylarıyla doğrulanır.
2. **Acil event seçimi.** Model bir acil event'i adayın numarasıyla seçer (`candidate`, 1'den başlar) ve yalnızca `rank`, `reason`, `checklist` yazar. `time`, `log_source`, `event_name`, `qid`, `source`, `destination`, `username`, `aql` ve `evidence_id`'yi `finalize` adaydan kopyalar.
   - Aralık dışındaki veya iki kez kullanılan numara `ModelRetry` ile geri gönderilir. Mesaj geçerli numaraları söyler.
   - `rank` 1'den başlar, ardışık ve benzersizdir; en fazla 15 kalem.
   - Test: geçerli seçim, aralık dışı numara, aynı aday iki kez, sıra hatası. Çıktıdaki `UrgentEvent`'in dokuz tanımlayıcısı adayınkiyle aynıdır.
3. **Gerçek kimlik yok.** Aday blokları adayın kanıtını `ev_c<n>` takma adıyla gösterir (`n`, `evidence` listesindeki sıra). Adayın `evidence_id`'si `evidence` listesinde yoksa `ReportingTask` doğrulaması reddeder.
   - Test: modelin gördüğü hiçbir mesajda (yeniden denemeler dahil) gateway kimliği (`ev_` + 32 hex) yoktur.
4. **Kaynak adları (T-48).** Claim'ler `agent.claim`, adaylar `agent.urgent_event`, data gap'ler `agent.data_gap` kaynağıyla sarılır. `qradar.offense` aynı kalır.
5. **Log metni.** `NoLogText` davranışı aynı kalır (20 karakterlik pencere, mesaj alıntıyı tekrar etmez); alıntıları `RunDeps`'ten okur. T-025'in kriter 7 testleri geçer.
6. **Dev stack ölçümü.** `test_reporting_dev_stack.py` silinir; yerine `tests/e2e/test_dev_reporting.py` gelir. Test yalnızca `AIS0C_DEV_STACK=1` ile koşar ve `soc-report` (D-44: Qwen 122B, `forced_tool_choice: false`) ile sentetik vakayı bir kez çalıştırır. PR'a koşunun durumu, token'ı, süresi ve özeti yazılır.

## Kapsam dışı

- Workflow bağlantısı (T-026)
- Özetin cümle sayısı ve Türkçe kalitesi (T-030)

## Bağımlılıklar

- T-046 birleşmiş olmalı (`agent.*` kaynakları).

## Notlar

- T-024'ün `RunDeps.reviewed_claim_texts` alanı vardır; Reporting kendi alanlarını ekler.
- Planner ölçümü (2026-10-06, Qwen 122B, eski tasarım): koşu tamamlandı, 22.882 token, yaklaşık 26 s, 320 karakterlik özet, 1 acil event, 2 öneri. Yeni tasarımda modelin yazdığı metin azaldığı için token düşmeli.
- Dev stack testinde LiteLLM için `LITELLM_BASE_URL=http://127.0.0.1:4000` ve `LITELLM_API_KEY` gerekir.
