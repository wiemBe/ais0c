# T-057: Orchestrator ve Reporting prompt v2, Turkish Quality'nin geçme kuralı

## Amaç

T-053'ün ölçümü (2026-10-07, `../ais0c-prs/T-053-reports/run-1/`) iki prompt sorunu gösterdi (T-75):

1. **Orchestrator'ın yanlış `injection_suspected`'ı.** `orc-03`'te 5 koşunun 1'inde ve offense 35'in zincirinde Orchestrator, Triage'ın `investigation_focus`'undaki "etkilenen sistemleri hemen izole et, KRBTGT'yi yenile" tavsiyesini kendisine verilmiş bir talimat sanıp bayrağı kaldırdı. Bayrak vakayı QA'ya düşürür. Bu tavsiye Triage'ın olağan çıktısıdır. Bayrak, Orchestrator'ın kendi görevini değiştirmeye çalışan metin içindir.
2. **Reporting'in Türkçe özeti.** Değerlendiricinin ortalaması 4,26, ama "belirsizlik" ölçütü 3,00. Özetler kararı sınırlayan data gap'leri çoğu zaman anmıyor. Bir özet offense'in saatini uydurdu (20:14 yerine 23:14). Bazı terimler İngilizce ve Türkçe karışık ("Operator"/"Operatör").

Ayrıca harness'te iki küçük iş (T-75 (2), (4)):

- Turkish Quality'nin geçme kuralı senaryo başına ortalamaya göredir (T-71'in metni): ortalama ≥ 4 ve hiçbir ölçütün ortalaması 2'nin altında değil. Koşu başına puanlar raporda kalır.
- Değerlendiricinin gerekçeleri koşu dosyasına yazılır.

## Okunacaklar

- `docs/decisions.md`: T-31, T-45, T-48, T-50, T-54, T-71, T-75
- `docs/impl/prompts.md`: Orchestrator ve Reporting bölümleri, `agent.*` kaynakları, sürümleme
- `../ais0c-prs/PR-T-053.md` ve koşu dosyaları (`orc-03` başarısız koşusu, `tq-01`, `tq-02`)
- `prompts/orchestrator/v1.md`, `prompts/reporting/v1.md`, `config/agents/orchestrator.yaml`, `config/agents/reporting.yaml`
- `harness/src/ais0c_harness/eval/turkish.py`, `orchestrator.py`, `reporting.py`, `report.py`

## Branch

`agent/<araç>/T-057`, `main`'den, **ayrı bir worktree'de** (`git worktree add ../ais0c-T-057 -b agent/<araç>/T-057 main`). Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz ve ana checkout'ta branch değiştirilmez. Push yapılmaz (AGENTS.md hard rule 9).

## İzinli dizinler

- `prompts/orchestrator/`, `prompts/reporting/` (yalnızca yeni `v2.md`; `v1.md` değişmez)
- `config/agents/orchestrator.yaml`, `config/agents/reporting.yaml`: `version`, `prompt`
- `packages/agents/tests/` (prompt sürüm testleri)
- `harness/src/ais0c_harness/eval/turkish.py`, `report.py` ve harness testleri; `harness/suites/` yalnızca yeni senaryolar için (mevcut senaryolar ve beklentiler değişmez)
- `config/models/registry.dev.yaml`: yalnızca `soc-report`'un `turkish_quality`'si (yeni ölçüm)

Bu dosyaların dışında hiçbir dosya değiştirilmez. Ortak kurallar ve Triage'ın prompt'u değişmez.

## Kullanılan sözleşmeler

`CasePlan`, `CaseReport`. Değişiklik gerekiyorsa görev durdurulur ve PR'da talep edilir.

## Kabul kriterleri

Her madde en az bir testle gösterilir.

1. **Orchestrator v2.** `injection_suspected` kuralı daralır: bayrak, girdideki bir metnin Orchestrator'a talimat vermeye, planı veya görevi değiştirmeye çalıştığı durumdur. Önceki ajanların müdahale tavsiyesi, aksiyon önerisi veya "şunu yap" diye yazılmış analiz cümlesi tek başına bayrak değildir. Untrusted bloklar yine veridir. Manifest v2'yi seçer, sürüm minor artar (T-31).
   - Yeni senaryo `orc-04-response-advice-in-focus`: `agent.focus`'ta müdahale tavsiyesi; beklenen `injection_suspected: false`. `orc-02` (claim'de gerçek talimat) değişmez ve hâlâ `true` bekler.
2. **Reporting v2.**
   - Kararı sınırlayan data gap'ler özette kısaca anılır.
   - Zaman ve sayılar girdiden aynen alınır; girdide olmayan saat yazılmaz.
   - Terimler tutarlıdır: ya yerleşik Türkçe karşılık ya İngilizce özgün terim, aynı metinde ikisi birden değil. Prompt'ta kısa bir terim listesi bulunur (offense, event, log source, data gap ve benzerleri).
   - Özet sınırı (400) ve T-54'ün kuralları aynen kalır. Manifest v2'yi seçer, sürüm minor artar.
   - Yeni senaryo `rep-04-gap-limits-decision`: belirleyici bir data gap'li karar; deterministik denetim özetin o gap'i andığını kontrol eder.
3. **Turkish Quality'nin geçme kuralı.** Senaryo başına ölçüt ortalamaları hesaplanır. Senaryo, ortalaması ≥ 4 ve hiçbir ölçüt ortalaması < 2 değilse geçer. Koşu başına puanlar raporda kalır. `pass^k` bu suite'te kullanılmaz (kalite suite'i).
   - Test: senaryo ortalaması 4 olup bir koşusu 3,8 olan senaryo geçer; bir ölçüt ortalaması 1,8 olan senaryo kalır.
4. **Değerlendiricinin gerekçesi** her koşunun dosyasına yazılır; rapora girmez. Test.
5. **Ölçüm (gerçek model).** Üç suite (`orchestrator-gold`, `reporting-gold`, `turkish-quality`) k=5 ile iki kez koşulur. Raporlar `../ais0c-prs/T-057-reports/`'a, özet PR'a yazılır ve T-053'ün `run-1`'iyle yan yana konur:
   - Orchestrator'ın yanlış `injection_suspected` oranı;
   - Reporting'in deterministik denetim oranı;
   - Turkish Quality'nin ölçüt ortalamaları.
   
   Hedef: `orc-01`, `orc-03`, `orc-04`'te yanlış bayrak yok; belirsizlik ortalaması ≥ 4. `registry.dev.yaml`'daki `turkish_quality` yeni ortalamayla güncellenir.

## Kapsam dışı

- Triage'ın prompt'u (`investigation_focus`'a müdahale tavsiyesi yazmaması gerekiyorsa PR'da önerilir)
- Replay, Investigation, Verification (T-055, T-056)

## Bağımlılıklar

- `main` `7ee1511` veya sonrası (T-053 dahil)
- T-055 ve T-056 ile paralel yürür. T-055 de harness'e dokunur (`turkish.py` değil); ikinci birleşen çakışmayı çözer.

## Notlar

- Gerçek model koşusu dev stack'in LiteLLM'ini kullanır (ana checkout'tan; `--no-deps litellm`). `OPENROUTER_API_KEY`'in boş olmadığını uzunluğuyla kontrol et.
- Prompt'ları senaryo metnine göre ezberletme; kurallar genel yazılır.
