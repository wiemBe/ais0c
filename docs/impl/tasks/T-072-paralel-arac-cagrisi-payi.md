# T-072: Paralel araç çağrısı payı ve harness hatasının ayrıntısı

## Amaç

T-065'in dcsync suite koşusunda (k = 5, `../ais0c-prs/T-065-reports/skill-dcsync-k5/`) 15 koşunun 3'ü cevapsız bitti. Sonuç veren 12 koşunun kararı doğruydu. Cevapsız koşuların nedenleri:

1. **İki koşu araç sınırında düştü:** `UsageLimitExceeded: The next tool call(s) would exceed the tool_calls_limit of 24 (tool_calls=25)`. 23 çağrı yapılmışken model tek cevapta iki araç çağırdı. `FinalAnswer` araçları ancak `tool_calls >= limit` olunca geri çekiyor (`packages/agents/src/ais0c_agents/runner.py`, `budget_spent`). 23'te araçlar hâlâ açıktı. Pydantic AI iki çağrılık grubu çalıştırmadan reddetti ve koşu cevapsız kaldı. DeepSeek V4 Flash `parallel_tool_calls`'u kabul etmediği için paralel çağrıyı kapatamıyoruz (planner.md §8).
2. **Bir koşu harness hatasıyla düştü:** `harness error: RecursionError: maximum recursion depth exceeded`. Koşu dosyasında traceback yok, bu yüzden nedeni bilinmiyor.

Bu görev (1)'i kapatır ve (2) için traceback'i kaydeder.

## Okunacaklar

Yalnızca bunlar:

- `docs/decisions.md`'de T-52 ve T-61 satırları (son cevap kuralı ve eşikleri)
- `packages/agents/src/ais0c_agents/runner.py`: modül docstring'i (satır 1-25), `budget_spent` (satır ~103-129), `FinalAnswer` (satır ~155-186)
- `packages/agents/tests/test_final_answer.py`: satır 178'deki `test_with_the_tool_call_budget_used_up_the_next_request_can_only_answer` (izlenecek desen) ve satır 301'deki test
- `harness/src/ais0c_harness/eval/runner.py:195-225` (`harness error` kaydı)

## Branch ve worktree

```bash
git worktree add ../ais0c-T-072 -b agent/<araç>/T-072 main
cd ../ais0c-T-072
```

Ana checkout'ta çalışılmaz. Push yapılmaz.

## İzinli dosyalar

- `packages/agents/src/ais0c_agents/runner.py`, `packages/agents/tests/test_final_answer.py`
- `harness/src/ais0c_harness/eval/runner.py` ve rapor yazan modül (koşu dosyasının alanı), `harness/tests/`

Prompt'lar, manifest'ler ve bütçe değerleri değişmez.

## Adımlar

1. **Paralel çağrı payı** (`runner.py`):

   ```python
   MIN_TOOL_BATCH: Final = 2
   """Tool calls one model response may ask for at once, at least: tools are withdrawn
   while that many still fit (the dev reasoning model cannot be told to call one at a time)."""

   def _largest_tool_batch(messages: Sequence[ModelMessage]) -> int:
       """The most ToolCallParts any ModelResponse in `messages` holds; 0 when none."""
   ```

   `budget_spent`'teki araç koşulu şu olur:

   ```python
   if limits.tool_calls_limit is not None:
       batch = max(MIN_TOOL_BATCH, _largest_tool_batch(messages))
       if limits.tool_calls_limit - usage.tool_calls < batch:
           return True
   ```

   - Araç bütçesi 0 olan ajan da `True` döner, bugünkü gibi.
   - `prompt_tool_budget` değişmez: prompt aynı sayıyı söyler, araçlar bir çağrı erken geri çekilir.
   - Modül ve `budget_spent` docstring'leri bu kuralı bir cümleyle anlatır (T-072).
2. **Testler** (`test_final_answer.py`), satır 178'deki testin `FunctionModel` deseniyle:
   - `test_a_parallel_batch_at_the_tool_limit_keeps_the_answer`: limit 24, model 22 tekli çağrı yapar, sonra iki çağrılık bir grup ister. Araçlar 23. çağrıdan önce geri çekilir, koşu `completed` olur ve cevabı vardır. Bugünkü kodla bu test `budget_exhausted`'la ve cevapsız biter; önce testi yaz, kırmızı olduğunu gör.
   - `test_a_larger_batch_seen_earlier_raises_the_reserve`: model 3'lü bir grup yaptıktan sonra kalan 2 çağrıda araçlar geri çekilir.
   - `test_sequential_runs_lose_one_call_at_most`: tekli çağıran bir model 24'lük bütçenin 23'ünü kullanır, sonra cevaplar.
   - Mevcut testlerin araç sayıları bu kurala göre değişiyorsa beklenti güncellenir. Her değişiklik PR'da gerekçesiyle listelenir.
3. **Harness hatası** (`harness/src/ais0c_harness/eval/runner.py`):
   - `except Exception as failure` dalı hatanın traceback'ini (`traceback.format_exception(failure)`) koşu dosyasına yazar. Yeni alan `error_traceback: str | None`, en çok son 50 satır.
   - Rapora girmez, yalnızca koşu dosyasına girer.
   - Test `test_a_harness_error_keeps_its_traceback`: `evaluate`'i `RecursionError` atan bir sahte adaptör; koşu dosyasında traceback var, `error` alanı bugünkü gibi.

## Kabul kriterleri

1. Adım 2'deki üç test geçer. Bugünkü `test_final_answer.py` ve `test_budget.py` testleri geçer; değişen beklentiler PR'da gerekçeli.
2. Adım 3'teki test geçer.
3. **Gerçek model ölçümü**, dev stack'in yalnızca `litellm`'iyle, ana checkout'tan değil worktree'den:

   ```bash
   set -a; . /home/efe/Documents/ais0c/deploy/compose/.env; set +a
   export LITELLM_API_KEY="$LITELLM_MASTER_KEY"
   uv run python -m ais0c_harness.eval run --suite skill-windows-dcsync --k 5 \
       --max-total-tokens 6000000 --out ../ais0c-prs/T-072-reports/skill-dcsync-k5
   ```

   Hedef: `pass^k` 3/3 ve `tool_calls_limit` hatasıyla biten koşu yok. Süre yaklaşık 30 dakikadır. Önce `OPENROUTER_API_KEY`'in boş olmadığını uzunluğuyla kontrol et. Geçmezse dur ve raporu PR'a yaz; eşikleri kendin değiştirme. `RecursionError` yeniden çıkarsa traceback'i PR'a koy.

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest packages/agents harness -q
uv run lint-imports
git grep -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

Dikkat: test adlarına ve yorumlara model ya da sağlayıcı adı yazılmaz (T-030'da CI bu yüzden kırıldı). Yorumda "the reasoning model" de.

## PR

`../ais0c-prs/PR-T-072.md`. İçinde şunlar bulunur: ölçümün özeti, T-065'in raporuyla yan yana (pass^k, budget_exhausted oranı, cevapsız koşu sayısı).

## Bağımlılıklar

- `main` (T-065 dahil). T-069 ve T-070 ile aynı anda yürüyebilir; dosyaları ayrı.

## Ek (2026-10-08 gece, planner): pay yetmiyor, grup kırpılır

> **Durum:** T-072 bu ek uygulanmadan bitti ve birleşti (T-102). Ek, sağlayıcı sabitlemesiyle birlikte T-073'e taşındı; o dosya geçerlidir.

Ajanın süren ölçümünün ilk dört koşusu (`../ais0c-prs/T-072-reports/skill-dcsync-k5/runs/sk-dcs-01-detect/`) dördü de `tool_calls_limit` hatasıyla cevapsız bitti. Model artık tek cevapta **27, 34, 42 ve 57** araç çağrısı istiyor. Bütün incelemeyi tek seferde planlıyor: henüz oluşturmadığı aramaların sonuçlarını, hatta `final_result`'u bile aynı grupta istiyor. T-062 ve T-065'in koşularında en büyük grup 4'tü (aynı skill metni, aynı gün 19:14). Kod prompt'u değiştirmiyor; dev LiteLLM OpenRouter'da sağlayıcıyı sabitlemiyor. Neden büyük ihtimalle sağlayıcı ya da model davranışındaki bir değişiklik. Hangi boyutta olursa olsun, "pay bırakma" yaklaşımı böyle bir grubu karşılayamaz.

**Karar (T-98, öneri): kalan bütçeyi aşan grup kırpılır.**

1. `FinalAnswer`'a `after_model_request` kancası eklenir (Pydantic AI 2.53, `AbstractCapability.after_model_request(ctx, *, request_context, response) -> ModelResponse`; cevap araçlar çalışmadan ve limit denetlenmeden önce değiştirilebilir). Cevaptaki `ToolCallPart` sayısı kalan araç bütçesini (`limits.tool_calls_limit - usage.tool_calls`) aşıyorsa yalnızca ilk N çağrı sırasıyla kalır, geri kalanı cevaptan çıkarılır. Diğer parçalar (metin, `final_result`) olduğu gibi kalır.
2. Kırpma olduysa bir sonraki istekte araçlar geri çekilir (bugünkü `FINAL_ANSWER_PROMPT` yolu). Kırpılan çağrı sayısı koşunun izine yazılır: `RunDeps`'te ya da log satırında `tool_calls_dropped`.
3. Kancanın girdisi yalnızca cevap ve sayaçlardır. Workflow kodunda çalışır ve deterministiktir; ağ, saat ya da rastgelelik kullanmaz.
4. Kırpma kesin olduğu için adım 1'deki `MIN_TOOL_BATCH` payı kalkar. Araçlar yine `tool_calls >= limit` olunca geri çekilir; sırayla çağıran model bütçesinin tamamını kullanır. Adım 2'deki testler buna göre değişir:
   - `test_a_parallel_batch_at_the_tool_limit_keeps_the_answer`: 23/24'te iki çağrılık grup → biri çalışır, koşu cevaplı biter;
   - yeni `test_a_batch_larger_than_the_budget_is_cut`: ilk cevapta 34 çağrı, bütçe 24 → 24'ü çalışır, sonraki istek araçsızdır, koşu `completed` ve cevaplı biter;
   - yeni `test_cut_keeps_the_final_result_part`: grupta `final_result` da varsa o kalır.
5. Ölçüm (kriter 3) aynı komutla yeniden koşulur. PR'a şunlar girer: `pass^k`, cevapsız koşu sayısı, kırpılan çağrı sayısı, en büyük grup ve T-062/T-065 ile karşılaştırma tablosu (en büyük grup 4'e karşı şimdiki). Kırpma koşuları kurtarsa da model çok büyük gruplarla kötü inceleme yapıyorsa (henüz oluşmayan aramaların sonuçlarını isteyen çağrılar) `pass^k` düşebilir. O zaman dur ve raporu yaz. Dev'de sağlayıcıyı sabitlemek (OpenRouter `provider.order`) planner'ın kararıdır.
