# T-062: Investigation, Verification ve plan bütçeleri: v2 prompt'larla ölçüm

## Amaç

T-055'in ölçümü v1 prompt'larla ve eski son cevap payıyla (2×) yapıldı. O ölçümde skill'li Investigation koşularının çoğu token sınırında (250.000) ya da 300 s süre sınırında düştü (T-81). T-056'dan beri Investigation ve Verification prompt v2 ile çalışıyor ve pay 3×. Bu görev T-81'in önerdiği bütçeleri uygular, v2 ile yeniden ölçer ve bütçeleri ölçüme göre kesinleştirir.

## Okunacaklar

- `docs/decisions.md`: T-41, T-61, T-80, T-81
- `../ais0c-prs/T-055-reports/run-1/report.md` ve `runs/`, `../ais0c-prs/PR-T-056.md`
- `config/agents/investigation.yaml`, `verification.yaml`, `skills/windows-dcsync/1.0.0/skill.yaml`, `packages/activities/src/ais0c_activities/settings.py` (`AIS0C_PLAN_*`)
- `tests/e2e/test_lab_investigation.py`, `test_lab_verification.py`

## Branch

`agent/<araç>/T-062`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-062 -b agent/<araç>/T-062 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `config/agents/investigation.yaml`, `config/agents/verification.yaml`: yalnızca `budgets`
- `skills/windows-dcsync/1.0.0/skill.yaml`: yalnızca `budgets`
- `packages/activities/src/ais0c_activities/settings.py` ve testi: yalnızca `AIS0C_PLAN_*` varsayılanları
- Bütçe değerlerini sabitleyen testler (`packages/agents/tests/`, `packages/activities/tests/`)
- `tests/e2e/test_lab_investigation.py`, `test_lab_verification.py`: yalnızca rapor satırı
- `harness/`: yalnızca koşu için; senaryolar ve beklentiler değişmez

Prompt'lar değişmez. Bu dosyaların dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

`Budget`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

1. **Başlangıç değerleri (T-81).**

   | Bütçe | Önce | Sonra |
   |---|---|---|
   | `windows-dcsync` skill'i, token | 250.000 | 300.000 |
   | `windows-dcsync` skill'i, süre | 300 s | 360 s |
   | Investigation, süre | 300 s | 360 s |
   | Verification, token | 120.000 | 150.000 |
   | `AIS0C_PLAN_TOKENS` | 440.000 | 480.000 |
   | `AIS0C_PLAN_SECONDS` | 480 | 600 |

   Değerleri sabitleyen testler güncellenir.
2. **Ölçüm** (gerçek model, dev LiteLLM, k = 5).
   - Suite'ler: `investigation-gold` (skill'li ve skill'siz), `verification-gold`, `skill-windows-dcsync`.
   - Raporlar `../ais0c-prs/T-062-reports/`'a yazılır.
   - PR'a senaryo başına şu tablo girer: tamamlanan koşu, `budget_exhausted` ve süre aşımı sayısı, token ve süre medyanı ile en çoğu, istek, araç çağrısı, çıktı düzeltmesi.
3. **Kesinleştirme.** Her bütçe, tamamlanan en büyük koşunun en az %20 üstündedir. Plan bütçesi plandaki ajanların (Investigation + Verification) toplamını karşılar. 1. kriterdeki değer bu kuralı sağlıyorsa kalır, sağlamıyorsa ölçümle değişir; gerekçe PR'dadır. Düşürme de aynı kuralla yapılır.
4. **Skill suite'inin gate'i.** `skill-windows-dcsync`'in `pass^k` sonucu ve başarısız koşuların nedeni (bütçe/süre veya yanlış cevap) PR'da yazılıdır. Yanlış cevaplar prompt bulgusudur; bu görevde düzeltilmez.
5. **E2e raporu.** Lab'daki Investigation ve Verification testleri, koşunun çıktı düzeltme sayısını (`output_retries`) da yazar (T-80 (2)). Bunu bir birim testi ya da raporun metnini doğrulayan bir test gösterir.

## Kapsam dışı

- Prompt değişiklikleri (T-60'ın sorgu önerisi ayrı bir prompt görevidir), skill'in içeriği ve onayı
- Prod modelleriyle ölçüm (T-031)

## Bağımlılıklar

- `main` (T-055, T-056 dahil)

## Notlar

- Investigation'ın koşusu dakikalar sürer. T-055'te `concurrency 2` ile toplam 8 milyon token harcandı; token tavanını (`--token-ceiling`) buna göre verin.
- T-055'te skill'siz Investigation skill'liden iyi sonuç verdi (5/5'e karşı 2/5). PR bu karşılaştırmayı v2 ile tekrar yazar.
