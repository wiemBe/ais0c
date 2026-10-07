# T-063: Triage prompt v4: engellenmiş trafik ve zararsız bağlam

## Amaç

Triage Gold (T-059, prompt v3) iki tutarlı hata gösterdi (T-83):

- **`tg-05`:** Dış bir tarayıcının bütün istekleri WAF'ta engellenmiş ve uygulamaya ulaşmamış. Model bunu 10 koşunun 10'unda `tp`/medium buldu. Saldırı imzasını saldırının kendisi saydı.
- **`tg-06`:** Onaylı iç tarayıcı. Model, varlık kaydını okumadığı koşularda "zararsız kayıt yok" dedi ve `tp` verdi. Kaydı okuduğu koşularda doğru cevap verdi (`fp`/low).

Bu görev Triage prompt v4'ü yazar ve Gold'un `cited_tools` beklentisini düzeltir. Güvenlik suite'leri bozulmamalıdır.

## Okunacaklar

- `docs/decisions.md`: T-52, T-74, T-78, T-80, T-83
- `prompts/triage/v3.md`, `docs/impl/prompts.md` (Triage ve ortak kurallar)
- `harness/suites/triage-gold/` (README, `tg-05`, `tg-06`), `../ais0c-prs/PR-T-059.md` ve `../ais0c-prs/T-059-reports/` (koşu dosyalarındaki gerekçeler)
- `harness/suites/adversarial-fn/`, `harness/suites/trust-layers/`

## Branch

`agent/<araç>/T-063`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-063 -b agent/<araç>/T-063 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `prompts/triage/` (yalnızca yeni `v4.md`; eski sürümler değişmez)
- `config/agents/triage.yaml`: `version` (1.3.0), `prompt`
- Prompt sürümünü sabitleyen testler (`packages/agents/tests/`, `packages/activities/tests/test_runtime.py`, `tests/e2e/`)
- `harness/suites/triage-gold/`: yalnızca `cited_tools` (kriter 3) ve README
- `harness/`: bunun dışında yalnızca koşu için

Ortak kurallar (`prompts/_shared/rules/v2.md`) değişmez. Değişiklik gerekiyorsa görev durdurulur ve PR'da önerilir.

## Kullanılan sözleşmeler

`TriageTask`, `TriageResult`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

1. **Engellenmiş trafik.** v4, engellendiği loglarda görünen trafiğin (WAF `blocked`, cevap kodu 0, firewall `deny`) etkisini ayrıca tartmayı söyler. Bir kaynağın bütün istekleri engellenmişse, hiçbiri uygulamaya ulaşmamışsa ve kaynaktan başka bir etkinlik yoksa, bu kendi başına bir etki göstermez: karar `fp` ya da düşük seviyedir. Aynı kaynaktan geçen tek bir istek, başarılı bir giriş ya da başka bir etkinlik bu kuralı bozar. Bu durumda engellenmemiş kısım tartılır. Kural yalnızca engellenmiş trafiğe dayanan vakalar içindir; engellenmemiş saldırıyı (`tg-03`, `tg-04`) yumuşatmaz.
2. **Zararsız bağlam okunmadan yokluk iddiası yok.** v4, "zararsız kayıt yok" ya da "onaylı etkinlik değil" gibi bir yokluk iddiasının, ilgili aracın (iç adres için varlık kaydı) sonucuna dayanmasını söyler. Araç çağrılmadıysa iddia yazılmaz; gerekiyorsa data gap yazılır.
3. **Gold'un `cited_tools`'u (T-83 (1)).** `get_offense`, `cited_tools`'tan çıkar. `cited_tools` yalnızca kararı belirleyen kanıtta kalır: `tg-06`'da `list_assets`. Değişen senaryoların sürümü değişir; suite testleri geçer.
4. **Ölçüm** (gerçek model, dev LiteLLM, k = 5, aynı gün).
   - Koşular: v3 ve v4 ile `triage-gold`, v4 ile `trust-layers` ve `adversarial-fn`.
   - Raporlar `../ais0c-prs/T-063-reports/`'a yazılır.
   - Beklenen:
     - güvenlik suite'leri `pass^k` ile geçer;
     - `tg-05` ve `tg-06` iyileşir;
     - hiçbir Gold senaryosunun geçme oranı v3'e göre 10 puandan fazla düşmez;
     - `decision_accuracy` ve `level_accuracy` v3'ten düşük değildir.
   - Sonuç PR'da senaryo başına tablodur.
5. **Testler.** v4'ün iki kuralı prompt testlerinde yer alır; v3'ün hash'i eski sürümler listesindedir.

## Kapsam dışı

- Investigation, Verification, Orchestrator ve Reporting prompt'ları (T-057 ayrı)
- T-60'ın sorgu önerisi (Investigation'ın işi)
- Gold senaryolarının metni ve karar beklentileri (`cited_tools` dışında)

## Bağımlılıklar

- `main` (T-059 dahil)

## Notlar

- `tg-05`'teki hatalı gerekçe örnekleri: "the attack activity is real and active", "blocking does not make it a false positive". Prompt bunları tek tek yasaklamaz; engellemenin etkiyi nasıl değiştirdiğini anlatır.
- RFC 5737 adresleri için prompt'a fixture notu yazılmaz (T-83 (3)).
