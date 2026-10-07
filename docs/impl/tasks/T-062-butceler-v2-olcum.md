# T-062: Ajan bütçeleri: token kaçak koruması, süre ölçümü (v2 prompt'lar)

## Amaç

Prod'da modeller on-prem çalışır; token'ın birim maliyeti yoktur. Bu yüzden token sınırı yalnızca kaçak korumasıdır (T-85). Bağlayıcı sınırlar şunlardır:

- süre (SLA);
- model isteği (`max_steps`);
- araç çağrısı.

T-055'in ölçümünde (v1 prompt'lar, 2× pay) skill'li Investigation koşularının çoğu token sınırında (250.000) ya da 300 s süre sınırında düştü (T-81).

Bu görev şunları yapar:

- T-85'in token sınırlarını ve T-81'in süre önerilerini uygular;
- v2 prompt'larla (T-056) dev'in maliyetini gözeterek yeniden ölçer;
- süre bütçelerini kesinleştirir.

## Okunacaklar

- `docs/decisions.md`: T-41, T-61, T-80, T-81, T-85
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

1. **Değerler (T-81 süreleri, T-85 token'ları).**

   | Bütçe | Önce | Sonra |
   |---|---|---|
   | Investigation, token | 300.000 | 600.000 |
   | Investigation, süre | 300 s | 360 s |
   | `windows-dcsync` skill'i, token | 250.000 | 600.000 |
   | `windows-dcsync` skill'i, süre | 300 s | 360 s |
   | Verification, token | 120.000 | 250.000 |
   | `AIS0C_PLAN_TOKENS` | 440.000 | 900.000 |
   | `AIS0C_PLAN_SECONDS` | 480 | 600 |

   Değerleri sabitleyen testler güncellenir. Triage, Orchestrator ve Reporting'in bütçeleri değişmez.
2. **Ölçüm** (gerçek model, dev LiteLLM).
   - Suite'ler ve k: `investigation-gold` (skill'li ve skill'siz) ile `verification-gold` k = 3; `skill-windows-dcsync` k = 5 (güvenlik suite'i, `pass^k`).
   - Komut bir `--max-total-tokens` ile koşar. Tavan PR'da gerekçelendirilir; T-055'in k = 5'lik tam koşusu 8 milyon token harcadı.
   - Raporlar `../ais0c-prs/T-062-reports/`'a yazılır.
   - PR'a senaryo başına şu tablo girer: tamamlanan koşu, `budget_exhausted` ve süre aşımı sayısı, token ve süre medyanı ile en çoğu, istek, araç çağrısı, çıktı düzeltmesi.
3. **Kesinleştirme.**
   - **Süre:** Her süre bütçesi, tamamlanan en büyük koşunun en az %20 üstündedir. Plan süresi plandaki ajanların (Investigation + Verification) toplamını karşılar. Zincirin toplam süresi (Triage + plan + Reporting) critical/high SLA'sıyla (10 dk) karşılaştırılıp PR'a yazılır.
   - **Token:** Token yüzünden `budget_exhausted` olan koşu varsa sınır yükselir. Sınır asla düşürülmez.
   - **İstek sınırı:** İstek sınırında (`max_steps`) biten koşular ayrı sayılır. Sayı yüksekse PR bunu öneri olarak yazar; sınır bu görevde değişmez.
4. **Skill suite'inin gate'i.** `skill-windows-dcsync`'in `pass^k` sonucu ve başarısız koşuların nedeni (süre, istek sınırı, yanlış cevap) PR'da yazılıdır. Skill'li ve skill'siz Investigation'ın karşılaştırması da yazılır; T-055'te skill'siz daha iyi sonuç vermişti. Yanlış cevaplar prompt bulgusudur, bu görevde düzeltilmez.
5. **E2e raporu.** Lab'daki Investigation ve Verification testleri, koşunun çıktı düzeltme sayısını (`output_retries`) da yazar (T-80 (2)). Bunu bir birim testi ya da raporun metnini doğrulayan bir test gösterir.

## Kapsam dışı

- Prompt değişiklikleri (T-60'ın sorgu önerisi ayrı bir prompt görevidir), skill'in içeriği ve onayı
- `max_steps` ve araç çağrısı sınırlarının değiştirilmesi
- Prod modelleriyle ölçüm (T-031)

## Bağımlılıklar

- `main` (T-055, T-056 dahil)

## Notlar

- Dev'in maliyeti gerçek model koşularının sayısından gelir; bir şey başarısız olunca bütün suite'i yeniden koşmak yerine yalnızca ilgili senaryoyu koşun (`--scenario`).
- Investigation'ın koşusu dakikalar sürer; `concurrency 2` T-055'te yeterliydi.
