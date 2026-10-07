# T-059: Triage Gold suite'i

## Amaç

Triage'ın kalitesini tek bir vakaya bağlı olmadan ölçmek (T-78). Bugün harness'te Triage'ın yalnızca güvenlik suite'leri var (Trust Layers, Adversarial FN); hepsi saldırgan metnine karşı dayanıklılığı ölçer. Doğru kararı veriyor mu sorusunun suite'i (Triage Gold, agent-harness §6) yok. Manifest `triage-gold`'u listeliyor ama suite yazılmadı.

Bu görev `triage-gold`'u `fixture` modunda yazar: T-78'deki sekiz senaryonun offense'i ve okuma araçlarının sonuçları senaryo dosyasında sentetik olarak bulunur. Lab gerekmez; T-058 ile paralel yürür.

| Senaryo | Beklenen karar | Beklenen seviye |
|---|---|---|
| `tg-01-kerberoasting` | `tp` veya `suspicious` | ≥ high |
| `tg-02-password-spraying` | `tp` veya `suspicious` | ≥ high |
| `tg-03-waf-sqli-gecti` | `tp` veya `suspicious` | ≥ high |
| `tg-04-waf-xss-gecti` | `tp` veya `suspicious` | ≥ medium |
| `tg-05-waf-tarama-engellendi` | `fp` veya `suspicious` | ≤ low |
| `tg-06-onayli-tarayici` | `fp` | ≤ low |
| `tg-07-dcsync` | `tp` veya `suspicious` | ≥ high |
| `tg-08-vpn-yeni-ulke` | `suspicious` | herhangi; en az bir data gap |

## Okunacaklar

- `docs/agent-harness.md` §6 (Triage Gold), §7, §8
- `docs/decisions.md`: T-27, T-64, T-67, T-74, T-78
- `harness/README.md` ("Eval runner"), `harness/src/ais0c_harness/eval/triage.py` (`TriageScenario`, beklentiler), `harness/suites/trust-layers/README.md` (biçim tablosu)
- `harness/scenarios/s2-dcsync.yaml`, `s3-vpn-yeni-ulke.yaml` ve T-058'in senaryo tablosu (görev dosyası)
- `../ais0c-prs/PR-T-030.md` (yorum 9: `layer`, `attack`, `marker` güvenlik suite'leri için zorunlu)

## Branch

`agent/<araç>/T-059`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-059 -b agent/<araç>/T-059 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `harness/` (senaryo biçimi, suite, testler, README); `harness/scenarios/` ve `loggen` değişmez (T-058'in)
- `config/agents/triage.yaml`: yalnızca `eval_suites` (zaten `triage-gold`'u listeliyorsa değişmez)

Bu dosyaların dışında hiçbir dosya değiştirilmez. Prompt değişmez.

## Kullanılan sözleşmeler

`TriageResult`, `OffenseSnapshot`, `EnrichmentContext`, `ToolResult`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir. Kriter 4 dışındaki testler gerçek model çağırmaz.

1. **Biçim.** Güvenlik dışı (kalite) Triage senaryosu için `layer`, `attack`, `marker` ve saldırıya özgü beklentiler isteğe bağlıdır (T-030 yorum 9). Yeni beklentiler: izin verilen kararlar, seviye aralığı (`min_level`, `max_level`), en az bir data gap gerekip gerekmediği, kanıt olarak gösterilmesi gereken araç sonuçları. Güvenlik suite'lerinin senaryoları ve beklentileri değişmeden geçer.
2. **Senaryolar.** Yukarıdaki sekiz senaryo `harness/suites/triage-gold/`'da (`kind: quality`, önek `tg-`). Her biri:
   - offense'in snapshot'ı ve zenginleştirmesi (katalog kaydı, kritik varlık eşleşmeleri; `tg-06`'da tarayıcı varlık listesinde iç tarayıcı olarak kayıtlıdır);
   - modelin büyük olasılıkla çağıracağı araçların sonuçları: `get_offense`, `get_rule`, `list_assets`, `list_offenses`; türetilen araçlar (T-67 (3)) senaryoda yazılmaz;
   - zararsız arka plan ile saldırıyı ayıran gerçekçi alanlar (4769'da `TicketEncryptionType`, ASM'de `request_status`, `attack_type`, `violations`).
   
   Adresler RFC 5737, adlar `example.com` ve lab'ın sentetik adlarıdır. Senaryo metni beklenen kararı söylemez.
   - Test: suite yüklenir; repo testi IP adreslerini tarar; scripted modelle her senaryo k=2 geçer.
3. **Raporda kalite metrikleri.** Suite için karar doğruluğu (izin verilen kararlar içinde olma), seviye doğruluğu ve beklenen data gap'in oranı raporlanır. `pass^k` kullanılmaz.
4. **Gerçek model ölçümü.** `triage-gold` k=5 ile iki kez koşulur (dev LiteLLM). Raporlar `../ais0c-prs/T-059-reports/`'a, özet PR'a yazılır: senaryo başına geçme oranı, karar ve seviye dağılımı, token, süre, `budget_exhausted`. Prompt değiştirilmez. Bir senaryonun düşük çıkması sonuçtur, düzeltilmez; nedeni koşu dosyalarından alıntıyla yazılır.

## Kapsam dışı

- Lab kayıtları ve replay senaryoları (T-058'den ve kuralların kurulmasından sonra)
- Prompt değişiklikleri
- Güvenlik suite'lerinin senaryoları

## Bağımlılıklar

- `main` `c9b54fa` veya sonrası
- T-058 ile paralel yürür (dosyaları ayrı). T-055 ve T-057 de harness'e dokunur; ikinci birleşen çakışmayı çözer.

## Notlar

- Gerçek model koşusu dev stack'in LiteLLM'ini kullanır (ana checkout'tan; `--no-deps litellm`). `OPENROUTER_API_KEY`'in boş olmadığını uzunluğuyla kontrol et.
- Sentetik araç sonuçları QRadar'ın gerçek alan adlarını ve biçimlerini kullanır (T-030'un senaryoları örnektir).
