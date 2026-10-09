# T-073: Dev'de OpenRouter sağlayıcısının sabitlenmesi ve bütçeyi aşan araç grubunun kırpılması

## Amaç

T-072'nin dcsync ölçümü (2026-10-08 akşam) geçersiz çıktı. Planner koşuların OpenRouter kimliklerini `generation` ucundan sorguladı:

| Ölçüm | `soc-reasoning`'i yanıtlayan sağlayıcı | En büyük araç grubu |
|---|---|---|
| T-062 (2026-10-07) | AtlasCloud (6/6) | 4 |
| T-065 (2026-10-08 19:14) | AtlasCloud (10/10) | 4 |
| T-072 (2026-10-08 23:00) | **Wafer** (19/19) | 27–59 |

Model aynı (`deepseek/deepseek-v4-flash-20260423`). Wafer'ın cevapları aynı çağrıları tekrarlayıp çıktı sınırında (8192 token) kesiliyor. T-057'nin ölçümleri AtlasCloud ve Novita'dan geldi; onlar geçerli.

Bu görev iki iş yapar (T-102, T-98):

1. **Sağlayıcı sabitlenir.** Dev ölçümleri karşılaştırılabilir olsun (T-64'ün gate koşulları) ve sağlayıcı, model sürüm kaydına (release) girsin. Sağlayıcı değişirse yeni bir release olur ve gate yeniden koşar.
2. **Bütçeyi aşan araç grubu kırpılır.** Hangi model olursa olsun, kalan araç bütçesinden büyük bir grup koşuyu cevapsız bırakmamalıdır.

## Okunacaklar

Yalnızca bunlar:

- `docs/decisions.md`'de T-98, T-102 ve T-103 satırları
- `config/litellm/litellm.dev.yaml` (bütün dosya; başındaki yorum OpenRouter yönlendirmesini anlatır)
- `config/models/registry.dev.yaml` (baştaki açıklamalar ve `soc-fast` girdisi)
- `packages/activities/src/ais0c_activities/model_release.py:30-110` (`REQUEST_SETTINGS`, `_ReleaseEntry`, `_release`)
- `tests/deploy/test_litellm_config.py:236-250`, `tests/deploy/test_model_registry.py`
- `packages/agents/src/ais0c_agents/runner.py`: `FinalAnswer` ve `budget_spent` (T-072'nin `MIN_TOOL_BATCH`'i dahil)
- `packages/agents/tests/test_final_answer.py` (T-072'nin testleri izlenecek desendir)
- Pydantic AI 2.53: `AbstractCapability.after_model_request(self, ctx, *, request_context, response) -> ModelResponse`. Cevap, araçlar çalışmadan ve limit denetlenmeden önce değiştirilebilir.

## Branch ve worktree

```bash
git worktree add ../ais0c-T-073 -b agent/<araç>/T-073 main
cd ../ais0c-T-073
```

Ana checkout'ta çalışılmaz. Push yapılmaz.

## İzinli dosyalar

- `config/litellm/litellm.dev.yaml`, `config/models/registry.dev.yaml`
- `packages/activities/src/ais0c_activities/model_release.py` ve testleri
- `tests/deploy/test_litellm_config.py`, `tests/deploy/test_model_registry.py`
- `packages/agents/src/ais0c_agents/runner.py`, `packages/agents/tests/test_final_answer.py`, `packages/agents/tests/test_budget.py`

`config/litellm/litellm.prod.yaml`, `registry.prod.yaml`, `packages/contracts` ve prompt'lar değişmez.

## Adımlar

Sırayla:

1. **LiteLLM dev config.** Her alias'ın `extra_body.provider`'ı şu olur:

   ```yaml
   extra_body:
     provider:
       require_parameters: true
       data_collection: deny
       order: [AtlasCloud]      # soc-fast, soc-reasoning
       allow_fallbacks: false
   ```

   `soc-verifier` için `order: [Alibaba]`, `soc-report` için `order: [Novita]`. Her alias, son geçerli ölçümünü yanıtlayan sağlayıcıya sabitlenir: Triage ve dcsync ölçümleri AtlasCloud, T-062'nin `verification-gold`'u Alibaba, T-057'nin Reporting ve Turkish Quality ölçümleri Novita. Dosyanın başındaki yorum iki maddeyle genişletilir:
   - `order` ve `allow_fallbacks`: ölçümler aynı sağlayıcıda koşsun diye sağlayıcı sabittir;
   - sağlayıcı kapalıysa istek düşer ve harness bunu altyapı hatası olarak bir kez yeniden dener (T-103).
2. **Registry.** Dev registry'deki her girdiye adım 1'deki sağlayıcıyla `provider` alanı eklenir (`soc-fast`/`soc-reasoning`: `AtlasCloud`, `soc-verifier`: `Alibaba`, `soc-report`: `Novita`). `quantization` satırındaki "varies with the provider OpenRouter picks for each request" yorumu "the pinned provider's; not visible through OpenRouter" olur. Prod registry'ye alan eklenmez; yokluğu `null` sayılır.
3. **Release** (`model_release.py`).
   - `_ReleaseEntry`'ye `provider: _Text | None = None` eklenir.
   - `REQUEST_SETTINGS`'e `"provider"` eklenir.
   - `_release`, `provider` `None` değilse `settings["provider"] = entry.provider` yazar (`parallel_tool_calls`'un deseni).
   - Modül docstring'i bunu bir cümleyle söyler.
4. **Kırpma** (`runner.py`, T-98). `FinalAnswer`'a şu kanca eklenir:

   ```python
   async def after_model_request(
       self, ctx: RunContext[RunDeps], *, request_context: ModelRequestContext, response: ModelResponse
   ) -> ModelResponse:
       """Cut a response whose tool calls exceed the remaining tool call budget (T-98)."""
   ```

   - `enabled` değilse ya da `ctx.usage_limits.tool_calls_limit` `None` ise cevap aynen döner.
   - Kalan bütçe `remaining = limit - ctx.usage.tool_calls`. Cevaptaki `ToolCallPart`'lar (output tool `final_result` hariç) `remaining`'den fazlaysa, yalnızca ilk `remaining` tanesi sırasıyla kalır. Diğer parçalar (metin, düşünce, `final_result`) yerinde ve sırasında kalır. Yeni bir `ModelResponse` döner (`dataclasses.replace(response, parts=...)`).
   - Output tool'un adı `ctx`'ten ya da ajanın output tool adından alınır. Bilinmiyorsa, araç adı ajanın function tool'ları arasında olmayan çağrılar kırpılmaz.
   - Kırpma olduysa bir log satırı yazılır: `logger.warning("run %s: cut %d tool calls beyond the budget", ...)`. Sayı `RunDeps`'te bir sayaçta tutulursa raporlanabilir (`tool_calls_dropped`); `RunDeps` değiştirilemiyorsa yalnızca log yeter, PR'da yazılır.
   - Kanca workflow kodunda çalışır: yalnızca cevabı ve sayaçları okur; ağ, saat ve rastgelelik yok.
   - T-072'nin `MIN_TOOL_BATCH` payı **kalır**. Sırayla çağıran modelde bir çağrılık kayıp kabul edilir; kırpma, payın karşılayamadığı büyük gruplar için kesin güvencedir.
5. **Ölçüm** (adım 6'daki kontrollerden sonra).

## Kabul kriterleri ve testler

1. **LiteLLM** (`tests/deploy/test_litellm_config.py`):
   - `test_dev_config_reaches_every_alias_through_openrouter` yeni bloğu bekler;
   - yeni `test_dev_config_pins_one_provider_per_alias`: her alias'ta `order` tek elemanlıdır ve `allow_fallbacks` `false`'tur;
   - negatif test: iki sağlayıcılı `order` ya da eksik `allow_fallbacks` reddedilir.
2. **Registry ve release** (`tests/deploy/test_model_registry.py`, `packages/activities/tests/test_model_release.py`):
   - `test_dev_registry_provider_matches_litellm`: registry'deki `provider`, LiteLLM'deki `order[0]`'a eşittir;
   - `test_release_carries_the_provider`: dev release'in `inference_params["provider"]`'ı `"AtlasCloud"`'dur; prod release'te bu anahtar yoktur;
   - negatif test: `inference_params`'ta `provider` tekrar yazılırsa `ModelReleaseError`.
3. **Kırpma** (`packages/agents/tests/test_final_answer.py`, `FunctionModel` ile):
   - `test_a_batch_larger_than_the_budget_is_cut`: bütçe 24, ilk cevapta 34 araç çağrısı. 24'ü çalışır, sonraki istek araçsızdır, koşu `completed` ve cevaplıdır.
   - `test_cut_keeps_the_final_result_part`: 10 araç çağrısı ve bir `final_result`, kalan bütçe 3. Üç çağrı kalır, `final_result` da kalır.
   - `test_a_batch_within_the_budget_is_untouched`: kalan 5, grup 3; cevap aynen döner.
   - `test_cut_is_deterministic`: aynı cevap ve sayaçla iki çağrı aynı sonucu verir.
4. **Ölçüm (gerçek model):** dev stack'in `litellm`'i bu worktree'deki yeni config'le yeniden başlatılır. Compose, config'i kendi dosyasının checkout'undan bağlar (`../../config/litellm/litellm.dev.yaml`), bu yüzden compose dosyası worktree'den, `.env` ana checkout'tan verilir:

   ```bash
   docker compose -p ais0c-dev --env-file /home/efe/Documents/ais0c/deploy/compose/.env \
       -f /home/efe/Documents/ais0c-T-073/deploy/compose/docker-compose.dev.yaml \
       up -d --no-deps --force-recreate litellm
   ```

   Ana checkout'tan koşulursa `main`'deki eski config yüklenir ve ölçüm geçersiz olur. Ölçümden sonra `litellm` böyle kalır; planner birleştirmede ana checkout'tan yeniden kurar.

   Ardından komut worktree'den koşulur:

   ```bash
   set -a; . /home/efe/Documents/ais0c/deploy/compose/.env; set +a
   export LITELLM_API_KEY="$LITELLM_MASTER_KEY"
   uv run python -m ais0c_harness.eval run --suite skill-windows-dcsync --k 5 \
       --max-total-tokens 6000000 --out ../ais0c-prs/T-073-reports/skill-dcsync-k5
   ```

   - Hedef: `pass^k` 3/3, cevapsız koşu yok.
   - PR'a şunlar girer: en büyük araç grubu, kırpma sayısı ve T-062/T-065/T-072 ile karşılaştırma.
   - Sağlayıcıyı doğrula: raporun birkaç cevabının `provider_response_id`'si OpenRouter'ın `GET https://openrouter.ai/api/v1/generation?id=<id>` ucunda `provider_name: AtlasCloud` göstermeli. İstek `Authorization: Bearer $OPENROUTER_API_KEY` başlığıyla yapılır; yalnızca kimlik gönderilir.
   - Geçmezse dur ve raporu yaz; eşik değiştirme.

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest -q          # tam suite, ~7 dk; Temporal test sunucusu takılırsa PID ile durdur ve yeniden koş
uv run lint-imports
git grep -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

Dikkat: sağlayıcı adları (AtlasCloud, Novita) ve model adları `config/litellm/`, `config/models/` ve config testleri (`tests/deploy/`) dışında geçmez. `packages/` altındaki kod, test ve yorumlarda yazılmaz (AGENTS.md hard rule 3); oradaki testler değeri registry dosyasından okur.

## PR

`../ais0c-prs/PR-T-073.md`.

## Bağımlılıklar

- `main` (T-072 dahil).
- T-071 ile aynı anda yürüyebilir: T-071 `agents/skills.py`'ye, bu görev `runner.py`'ye dokunur.

## Notlar

- Release değiştiği için `python -m ais0c_harness.eval releases --registry config/models/registry.dev.yaml` dört alias'ı "değişti" diye listeler. Bu beklenen bir sonuçtur.
- Prod'da sağlayıcı yoktur (on-prem vLLM); `provider` alanı prod registry'de yazılmaz.
