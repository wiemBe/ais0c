# T-056: Son cevabın düzeltme payı ve ajan prompt'larında alan sınırları

## Amaç

Zincirin lab e2e'sinde (2026-10-07, offense 36, `main` `8d33c73`) Verification yine `budget_exhausted` ile sonuçsuz bitti: `Exceeded the total_tokens_limit of 120000 (total_tokens=122977)`. T-051'in eşiği bu kez doğru çalıştı ama yetmedi. İstek başına token'lar (Temporal geçmişi, `case-36-verification-1`):

| İstek | Girdi | Çıktı | Toplam (birikimli) | Ne yaptı |
|---|---|---|---|---|
| 1–9 | 7.424 → 13.828 | 122–756 | 96.695 | 3 Ariel araması (`create`, `status`, `results`), 1 Guard reddi |
| 10 | 11.126 | 2.255 | 110.076 | araçlar geri çekilmiş, `final_result` |
| 11 | 11.927 | 974 | **122.977** | `final_result`'ın düzeltmesi; sınırı aştı, cevap kayboldu |

10. istekteki cevap şemadan geçmedi: `disagreements[0].reason` 300 karakteri aştı (`string_too_long`). Pydantic AI düzeltme istedi ve o istek bütçeyi aştı. Bu, T-051 PR'ının açık soru 1'de öngördüğü durumdur (T-61): çıktı yeniden denemesi rezervde yok. Triage'da aynı hatanın karşılığı `rationale`'in 600 karakteri aşmasıdır (T-030, T-054).

Sonuçsuz Verification her vakada `verifier_conflict` QA'sı açar ve zincirin kontrolünü boşa çıkarır. Aynı mekanizma istek sınırında da çalışır: T-054'ün harness koşularında Triage koşularının çoğu `request_limit`'e (8) takıldı, çünkü düzeltme istekleri araç turlarını yedi (T-74). Düzeltme iki yerden yapılır:

1. **Rezerv:** araçlar geri çekilirken bir düzeltme isteğine de yer kalır; hem token bütçesinde hem istek sınırında (`max_steps`).
2. **Prompt:** Verification ve Investigation prompt'ları çıktı alanlarının sınırlarını açıkça söyler, böylece düzeltme isteği seyrekleşir.

## Okunacaklar

- `docs/decisions.md`: T-52, T-56, T-61, T-67 (4)
- `packages/agents/src/ais0c_agents/runner.py` (`budget_spent`, `_next_request_tokens`, `TOKEN_RESERVE_FACTOR`), `builder.py`
- `../ais0c-prs/PR-T-051.md` (neden analizi ve açık soru 1), `../ais0c-prs/PR-T-048.md`
- `packages/contracts/`: `VerificationResult`, `InvestigationResult` ve alanlarının uzunluk sınırları
- `prompts/verification/v1.md`, `prompts/investigation/v1.md`, `docs/impl/prompts.md` (sürümleme)
- Dev Temporal'da `case-36-verification-1`'in geçmişi (`temporal workflow show --workflow-id case-36-verification-1 --output json`, admin-tools container'ında)

## Branch

`agent/<araç>/T-056`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-056 -b agent/<araç>/T-056 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `packages/agents/src/ais0c_agents/runner.py` ve `packages/agents/tests/`
- `prompts/verification/` ve `prompts/investigation/` (yalnızca yeni `v2.md`; `v1.md` değişmez)
- `config/agents/verification.yaml`, `config/agents/investigation.yaml`: `version`, `prompt`
- `tests/e2e/test_lab_verification.py`, `tests/e2e/test_lab_investigation.py`: yalnızca ölçüm
- `harness/`: yalnızca koşu için (kriter 5); senaryolar ve beklentiler değişmez

Bu dosyaların dışında hiçbir dosya değiştirilmez. Triage'ın prompt'u T-054'tedir; ortak kurallar değişmez.

## Kullanılan sözleşmeler

`VerificationResult`, `InvestigationResult`, `DataGap`, `RunStatus`. Sınırlar değişmez. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Yeniden üretim.** Bir `ScriptedModel` testi case-36'nın 11 isteğini kayıtlı token'larıyla oynar: 10. istekte `reason`'ı 301 karakterlik bir cevap, 11. istekte düzeltilmiş cevap. Düzeltmeden önce test `budget_exhausted` ile biter (sonuç yok).
2. **Düzeltme payı.** `budget_spent` araçları, kalan bütçe bir araç çağrısına, cevaba ve bir düzeltme isteğine yetmeyecekken geri çeker. Payın hesabı PR'da gerekçelendirilir (örnek: `TOKEN_RESERVE_FACTOR` 3, ya da cevap ve düzeltme için son isteğin büyüklüğü artı çıktı payı).
   - Test: kriter 1'in senaryosu sonuçla ve `budget_exhausted` data gap'iyle tamamlanır.
   - T-048, T-049 ve T-051'in `test_final_answer.py` testleri geçer. Bir test yeni payla anlamını yitiriyorsa nedeniyle güncellenir.
   - Bu payın bedeli de ölçülür: T-051'in case-34 senaryosunda ve kriter 1'de araçların kaçıncı istekte geri çekildiği PR'a yazılır.
   - **İstek sınırı (T-56'nın üçüncü koşulu):** araçlar, iki istek hakkı (cevap ve bir düzeltme) kaldığında geri çekilir; bugün bir hak kaldığında çekiliyor. Test: `max_steps` sınırında, ilk cevabı şemadan geçmeyen bir koşu düzeltmeyle tamamlanır.
3. **Prompt'lar.** `prompts/verification/v2.md` ve `prompts/investigation/v2.md`: çıktının serbest metin alanlarının karakter sınırları (sözleşmedeki değerler) ve kısa yazma talimatı. Sınırlar prompt'a elle yazılırsa bir test sözleşmedeki değerlerle aynı olduklarını doğrular. Manifest'ler v2'yi seçer, sürümler `1.1.0` olur (T-31). `test_prompts.py`'nin eski sürüm hash'leri listesine v1'ler girer.
4. **Lab ölçümü (planner'ın verdiği kapalı offense: `QRADAR_LAB_OFFENSE_ID=30`).** `tests/e2e/test_lab_verification.py` en az üç kez, `tests/e2e/test_lab_investigation.py` en az iki kez koşulur. PR'a her koşunun durumu, token'ı, araçların geri çekilip çekilmediği ve çıktı düzeltme sayısı yazılır. Test offense açmaz, kapatmaz, not yazmaz.

5. **Triage'ın güvenlik suite'leri (T-74).** Bu değişiklikten ve T-052'nin türetilmiş cevaplarından sonra T-030'un komutu iki kez koşulur (`--suite trust-layers --suite adversarial-fn --k 5 --concurrency 8`). Raporlar `../ais0c-prs/T-056-reports/`'a, özet tablo PR'a (T-054'ün tablosu gibi: tamamlanan koşu, `budget_exhausted`, gate'te düşen senaryolar) yazılır. Aynı gün `main`'in önceki hâliyle (bu görevin değişikliği olmadan) bir koşu daha alınır, böylece sağlayıcı sapması ayrılır. Hedef gate'in geçmesidir; geçmezse neden koşu dosyalarından alıntıyla yazılır, prompt değiştirilmez.

## Kapsam dışı

- Triage'ın prompt'u (T-054)
- Bütçelerin kendisi ve ajan başına `budget_exhausted` oranı (T-055)
- Çıktının kırpılarak kabul edilmesi: model metni kod tarafından kesilmez; düzeltme modele bırakılır

## Bağımlılıklar

- `main` `7ee1511` veya sonrası (T-052, T-053, T-054 dahil)
- T-052, T-053 ve T-054 `main`'dedir (2026-10-07 akşam). T-055 ve T-057 ile paralel yürür; T-057 Orchestrator ve Reporting prompt'larına, T-055 harness'e dokunur.

## Notlar

- Lab ölçümü dev stack'i kullanır (ana checkout'tan; Postgres, Temporal, LiteLLM, gateway). Dev gateway'in `qradar-mcp-read`'e ulaşması `qradar-vmnet` ağını ister. Ağ kurulu değilse planner'a bildirilir (Docker libvirt'ten önce açılırsa ağ bulunamaz).
- Aynı e2e'de Triage DCSync senaryosunu `fp` verdi (offense 34'ten sonra ikinci kez). Bu T-054'ün konusudur, bu görevin değil.
