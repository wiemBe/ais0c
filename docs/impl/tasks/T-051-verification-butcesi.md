# T-051: Verification'ın bütçe aşımı ve son cevap eşiği

## Amaç

Zincirin lab e2e'sinde (2026-10-06, offense 34, `main` `530c01e`) Verification `budget_exhausted` ile sonuçsuz bitti: `Exceeded the total_tokens_limit of 80000 (total_tokens=82377)`, 6 araç çağrısı, 47,5 s. T-52'nin son cevap kuralı (`FinalAnswer`) araçları geri çekip modeli sonuca zorlamalıydı. Sonuçsuz Verification her vakada `verifier_conflict` QA'sı açar. Neden bulunur ve düzeltilir.

## Okunacaklar

- `docs/decisions.md`: T-52, T-56
- `packages/agents/src/ais0c_agents/runner.py` (`budget_spent`, `FinalAnswer`, `TOKEN_RESERVE_FACTOR`), `builder.py`
- `../ais0c-prs/PR-T-048.md` (son cevap, açık soru 3), `../ais0c-prs/PR-T-049.md`
- Dev veritabanında `agent_runs.run_id = 'case-34-verification-1'` ve onun `tool_calls`'ı (planner'ın e2e'si; `deploy/compose/.env` ile bağlanılır)

## Branch

`agent/<araç>/T-051`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-051 -b agent/<araç>/T-051 main`). Ana checkout'ta çalışılmaz. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `packages/agents/src/ais0c_agents/runner.py`, `packages/agents/tests/`
- `config/agents/verification.yaml`: yalnızca `budgets`
- `packages/activities/src/ais0c_activities/settings.py` ve testi: yalnızca `AIS0C_PLAN_TOKENS`'ın varsayılanı, Verification bütçesi değişirse
- `tests/e2e/test_lab_verification.py`: yalnızca ölçüm

Bu dosyaların dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

`DataGap`, `RunStatus`, `VerificationResult`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Neden.** PR, case-34'ün Verification koşusunda eşiğin neden tetiklenmediğini veya tetiklenip neden yetmediğini açıklar (istek başına token, son isteğin büyüklüğü, `budget_spent`'in hesabı). Bir `ScriptedModel` testi aynı durumu yeniden üretir ve düzeltmeden önce başarısız olur.
2. **Düzeltme.** Eşik, tek bir isteğin büyüklüğünün kalan bütçeyi aştığı durumda da araçları zamanında geri çeker (örneğin bir sonraki isteğin, son isteğin token'ı artı son araç sonucunun büyüklüğü kadar olacağı varsayımıyla). Test: kriter 1'in senaryosu sonuçla ve `budget_exhausted` data gap'iyle tamamlanır. T-048 ve T-049'un `test_final_answer.py` testleri geçer.
3. **Bütçe.** Ölçüm gerektiriyorsa Verification'ın token bütçesi artırılır (80000'den en çok 120000'e) ve plan bütçesinin varsayılanı Investigation + Verification'ı karşılayacak şekilde güncellenir. Gerekçe PR'a yazılır.
4. **Lab (planner'ın verdiği kapalı offense: `QRADAR_LAB_OFFENSE_ID=30`).** `tests/e2e/test_lab_verification.py` en az iki kez koşulur; durum, token ve araçların geri çekilip çekilmediği PR'a yazılır. Test offense açmaz, kapatmaz, not yazmaz.

## Kapsam dışı

- Investigation'ın bütçesi (T-030)
- Prompt değişiklikleri

## Bağımlılıklar

- `main` `530c01e` veya sonrası

## Notlar

- T-045 ve T-050 ile paralel yürür; dosyaları çakışmaz (T-045 `settings.py`'ye başka satırlar ekleyebilir).
