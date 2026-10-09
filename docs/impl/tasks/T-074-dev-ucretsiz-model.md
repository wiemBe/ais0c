# T-074: Dev'de ücretsiz model yedeği: OpenCode Zen'in `space-bunny-free`'si (ikinci LiteLLM config'i)

## Amaç

OpenRouter kredisi 2026-10-09'da bitti; kullanıcı OpenRouter'ın kullanılmamasını ve yerine ücretsiz bir modelin kullanılmasını istedi (T-104). Bu görev dev LiteLLM'e **ikinci bir config** ekler: dört alias da OpenCode Zen'deki ücretsiz `space-bunny-free`'ye gider. OpenRouter config'i (`litellm.dev.yaml`, T-073'ün sağlayıcı sabitlemesiyle) olduğu gibi kalır. Hangisinin yükleneceğini compose'da tek bir değişken seçer. Kredi gelince değişken boşaltılır, OpenRouter'a dönülür.

Planner'ın ölçümü (2026-10-09, `https://opencode.ai/zen/v1/chat/completions`, anahtar `public`, projeye ait veri gönderilmeden):

| Deneme | Sonuç |
|---|---|
| Araç çağrısı | çalışıyor |
| `tool_choice: "required"` ve adıyla zorlanan araç | çalışıyor; iç içe şemalı `final_result` geçerli JSON |
| `parallel_tool_calls: false` | kabul ediliyor ama uygulanmıyor (iki şehir için yine iki çağrı) |
| Bağlam | 252.663 token'lık istek kabul edildi |
| Diğer ücretsiz Zen modelleri | 403 `FreeTierError` ("free tier can only be used from within OpenCode"); `exo-free` 410 (kullanımdan kalktı) |
| Python `urllib`'in varsayılan `User-Agent`'ı | Cloudflare 403 (1010). `OpenAI/Python`, `httpx`, `litellm`, `aiohttp` geçiyor |

Bu config'le alınan ölçümler prod modelleriyle karşılaştırılamaz: gate ve prompt kabulü için kullanılmaz, yalnızca akışın uçtan uca çalıştığını gösterir (T-104).

## Okunacaklar

Yalnızca bunlar:

- `docs/decisions.md`'de T-103 ve T-104 satırları
- `config/litellm/litellm.dev.yaml` (bütün dosya; yeni dosyanın deseni)
- `config/models/registry.dev.yaml` (baştaki açıklamalar ve `soc-fast` girdisi)
- `deploy/compose/docker-compose.dev.yaml:141-170` (`litellm` servisi)
- `deploy/compose/.env.example:12-17`
- `tests/deploy/test_litellm_config.py:1-60`, `:121-175`, `:240-290`
- `tests/deploy/test_model_registry.py:17-60`, `:116-200`
- `tests/deploy/test_compose_file.py:25-40` (`INTERPOLATION`, `load_compose`), `:260-275` (`secret_problems`), `:415-440` (bind mount testleri)
- `tests/e2e/e2e_support.py:20-30`

## Branch ve worktree

```bash
git worktree add ../ais0c-T-074 -b agent/<araç>/T-074 main
cd ../ais0c-T-074
```

Ana checkout'ta çalışılmaz, branch değiştirilmez, push yapılmaz. `packages/contracts`'a ve izinli dosyalar dışına dokunulmaz.

## İzinli dosyalar

- Yeni: `config/litellm/litellm.dev-free.yaml`, `config/models/registry.dev-free.yaml`
- `deploy/compose/docker-compose.dev.yaml` (yalnızca `litellm` servisi), `deploy/compose/.env.example`, `deploy/compose/README.md`
- `tests/deploy/test_litellm_config.py`, `tests/deploy/test_model_registry.py`, `tests/deploy/test_compose_file.py`
- `tests/e2e/e2e_support.py`

`litellm.dev.yaml`, `registry.dev.yaml`, prod dosyaları, `packages/`, `harness/` ve prompt'lar değişmez. Harness'in `run` ve `releases` komutlarında `--registry` zaten var; kod değişikliği gerekmez.

## Adımlar

Sırayla:

1. **`config/litellm/litellm.dev-free.yaml`.** Başında yorum: ne olduğu (T-104), D-21'in bilerek bozulduğu (dört alias aynı model; ücretsiz katmanda dışarıdan çağrılabilen tek model bu), ölçümlerin prod'la karşılaştırılamadığı, yalnızca sentetik ve lab verisinin gönderileceği, ücretsiz uç noktanın prompt'ları saklayabileceği. Dört alias, her biri tek deployment:

   ```yaml
   model_list:
     - model_name: soc-fast
       litellm_params:
         model: openai/space-bunny-free
         api_base: https://opencode.ai/zen/v1
         api_key: os.environ/OPENCODE_ZEN_API_KEY
   ```

   `soc-reasoning`, `soc-verifier`, `soc-report` aynısı. `extra_body`, `fallbacks` ve router ayarı yok.

2. **`config/models/registry.dev-free.yaml`.** Başında `registry.dev.yaml`'daki gibi alan açıklamaları (kısa), farkları açıkça: hedef ücretsiz model, `provider` yok (sağlayıcı yönlendirmesi yok). Dört alias için aynı değerler:

   ```yaml
   soc-fast:
     target: openai/space-bunny-free
     prod_equivalent: <registry.prod.yaml'daki aynı alias'ın target'ı, aynen>
     capabilities: [tool_calling, structured_output]
     parallel_tool_calls: null  # accepted but not honoured (false still returned two calls, 2026-10-09)
     forced_tool_choice: true
     context_window: 250000  # a 252,663-token request was accepted (2026-10-09); the real limit is not published
     tool_parser: null
     reasoning_parser: null
     turkish_quality: null
     artifact: space-bunny-free
     artifact_hash: null
     quantization: null
     tokenizer: null
     engine_version: null
     inference_params: {}
   ```

3. **Compose (`litellm` servisi).**
   - Bind mount: `- ../../config/litellm/${AIS0C_LITELLM_CONFIG:-litellm.dev.yaml}:/etc/litellm/config.yaml:ro,z`
   - `environment`'a: `OPENCODE_ZEN_API_KEY: ${OPENCODE_ZEN_API_KEY:-}` (boş varsayılan; `secret_problems` başka bir varsayılanı reddeder).
   - Servisin üstündeki yorum: `AIS0C_LITELLM_CONFIG=litellm.dev-free.yaml` ücretsiz modele geçirir (T-104).

4. **`.env.example`.** `OPENROUTER_API_KEY`'in altına, yorumlarıyla:

   ```text
   # Which LiteLLM config the dev proxy loads (T-104). Empty: litellm.dev.yaml (OpenRouter).
   # litellm.dev-free.yaml sends every alias to OpenCode Zen's free model.
   AIS0C_LITELLM_CONFIG=
   # OpenCode Zen key for litellm.dev-free.yaml. The free tier accepts the literal "public".
   OPENCODE_ZEN_API_KEY=
   ```

5. **`README.md` (deploy/compose).** `litellm` satırına ve kısa bir paragrafa: iki config, değişken, geçiş komutu (adım 7'deki), worker'ın ve harness'in hangi registry'yi alacağı.

6. **`tests/e2e/e2e_support.py`.** `MODEL_REGISTRY` sabit kalmaz: `os.environ.get("AIS0C_E2E_MODEL_REGISTRY", "config/models/registry.dev.yaml")`. Modül yorumuna bir satır.

7. **Testler** (aşağıdaki kriterler). Gerçek model çağrısı yapılmaz; Zen'e istek gönderilmez.

Geçiş komutu (README'ye ve PR'a yazılır, bu görevde **koşulmaz**; planner koşar):

```bash
docker compose -p ais0c-dev --env-file /home/efe/Documents/ais0c/deploy/compose/.env \
    -f deploy/compose/docker-compose.dev.yaml up -d --no-deps --force-recreate litellm
```

## Kabul kriterleri ve testler

1. **Config** (`tests/deploy/test_litellm_config.py`):
   - `ENVIRONMENTS`'a `"dev-free"` eklenir; `test_config_defines_each_alias_once` ve `test_keys_come_from_the_environment` onu da kapsar.
   - `test_reasoning_and_verifier_use_different_models` yalnızca `["dev", "prod"]` için koşar (ayrı bir parametre listesi); dev-free bilerek dışarıdadır.
   - `test_free_dev_config_routes_every_alias_to_opencode_zen`: dört alias'ın da `model`'i `openai/space-bunny-free`, `api_base`'i `https://opencode.ai/zen/v1`, `api_key`'i `os.environ/OPENCODE_ZEN_API_KEY`; `extra_body` yok.
   - `test_free_dev_config_reads_no_openrouter_key`: dosyada `OPENROUTER` geçmez.
   - Negatif: `test_free_dev_config_with_an_openrouter_deployment_is_reported`: bir alias `openrouter/...` modeline çevrilmiş kopya, testin yardımcı fonksiyonunda hata verir.
2. **Registry** (`tests/deploy/test_model_registry.py`):
   - `test_registry_describes_every_alias_of_its_litellm_config` dev-free'yi de kapsar (registry'nin `target`'ı, LiteLLM'deki `model`'e eşit).
   - `test_free_dev_registry_has_no_provider`.
   - `test_free_dev_registry_names_the_prod_equivalents`: her alias'ın `prod_equivalent`'i, `registry.prod.yaml`'daki aynı alias'ın `target`'ına eşit.
   - `test_dev_registry_runs_the_prod_models` ve `test_dev_registry_provider_matches_litellm` yalnızca dev için kalır.
3. **Compose** (`tests/deploy/test_compose_file.py`):
   - Bind mount kaynağındaki `${NAME:-default}` varsayılanla çözülür (dosyadaki `INTERPOLATION` ile küçük bir yardımcı); mevcut "kaynak var mı" testi ve `test_litellm_runs_the_dev_config` değişken boşken `litellm.dev.yaml`'ı bulur.
   - `test_litellm_config_variable_selects_the_free_config`: `AIS0C_LITELLM_CONFIG=litellm.dev-free.yaml` ile çözülen kaynak `config/litellm/litellm.dev-free.yaml`'dır ve dosya vardır.
   - `test_secrets_come_only_from_the_environment` `OPENCODE_ZEN_API_KEY`'le de geçer.
   - `.env.example` ile compose'daki değişkenleri karşılaştıran bir test varsa, iki yeni değişkenle geçer.
4. **E2E yardımcısı:** `test_e2e_registry_comes_from_the_environment` (`tests/deploy/` altında ya da `tests/e2e/`'nin gerçek model gerektirmeyen bir testinde; varsayılan `registry.dev.yaml`, değişkenle dev-free). Bu test gerçek model ya da lab istemez ve atlanmaz.

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest tests/deploy -q
uv run pytest -q          # tam suite, ~7 dk; Temporal test sunucusu takılırsa PID ile durdur ve yeniden koş
uv run lint-imports
git grep -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu|space-bunny|opencode' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

Model ve sağlayıcı adları (`space-bunny-free`, `opencode`) yalnızca `config/litellm/`, `config/models/`, `tests/deploy/` ve compose dosyalarında geçer (AGENTS.md hard rule 3).

## PR

`../ais0c-prs/PR-T-074.md`. Geçiş komutunu ve dönüş yolunu (değişkeni boşalt, aynı komut) PR'a yaz.

## Kapsam dışı

- Gerçek model ölçümü ve LiteLLM'in yeniden başlatılması (planner yapar).
- `harness/src/ais0c_harness/eval/from_records.py:515`'teki sabit `registry.dev.yaml` (kayıt üretimi; ayrı iş).
- Prompt'lar, bütçeler, `packages/`.

## Bağımlılıklar

- T-073 `main`'de (registry'deki `provider` alanı ve testleri).

## Notlar

- **Gerçek model komutu koşma.** OpenRouter kredisi yok; Zen'e de bu görevde istek gönderilmez.
- Sözleşme değişikliği, izinli dosya dışı ihtiyaç ya da dokümanla çelişki çıkarsa dur ve PR'da yaz; tahmin yürütme.
- Planner birleştirmeden sonra dev `.env`'e `AIS0C_LITELLM_CONFIG=litellm.dev-free.yaml` ve `OPENCODE_ZEN_API_KEY=public` yazar, LiteLLM'i yeniden kurar, T-073'ün ölçüm komutunu `--registry config/models/registry.dev-free.yaml` ile koşar.
