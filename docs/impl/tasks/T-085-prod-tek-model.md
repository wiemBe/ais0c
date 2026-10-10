# T-085: Prod'da tek model: bütün alias'lar DeepSeek V4 Flash

## Amaç

Karar D-45 (kullanıcı, 2026-10-10): bankanın içinde yapay zekâ olarak yalnızca on-prem LiteLLM üzerinden **DeepSeek V4 Flash** var; Qwen 122B yok. Bugün prod config'i iki model varsayıyor (D-21): `soc-fast`/`soc-reasoning` DeepSeek V4 Flash, `soc-verifier`/`soc-report` Qwen 122B. Bu görev dört alias'ı da DeepSeek V4 Flash'a çevirir, Qwen'in ortam değişkenlerini prod compose'tan kaldırır ve "reasoning ile verifier farklı modelde" kuralını (artık sağlanamaz) testlerden çıkarır. Dev, D-12 gereği aynı eşlemeyi kullanır.

`soc-embed` kullanılmıyor (`config/litellm/litellm.dev.yaml:7`); bu görevin dışında. Verification prompt'unun "different model" cümlesi T-066'da (v3) kalkıyor; bu görev prompt'a dokunmaz.

## Okunacaklar

- `docs/decisions.md`: D-12, D-21, D-44, D-45, T-103, T-104
- `config/litellm/litellm.prod.yaml` (bütün dosya), `config/models/registry.prod.yaml:48-110`
- `config/litellm/litellm.dev.yaml:30-75`, `config/models/registry.dev.yaml` (bütün dosya; `soc-reasoning`'in sağlayıcı sabitlemesi T-103)
- `config/models/registry.dev-free.yaml:40-70` (`prod_equivalent`)
- `deploy/compose/docker-compose.prod.yaml:184-200` (`litellm`), `:318-340` (`preflight`); `deploy/compose/.env.prod.example:20-30`
- `tests/deploy/test_litellm_config.py:124-160` (`test_reasoning_and_verifier_use_different_models`, `test_shared_model_between_reasoning_and_verifier_is_detected`), `tests/deploy/test_model_registry.py:117-215`, `tests/deploy/test_prod_compose.py` (VLLM değişkenlerini sayan testler, `test_env_example_lists_every_variable`)
- `services/worker/src/ais0c_worker/preflight.py` ve `model_release.py`: `VLLM_*` adlarını sabit yazıyorlarsa
- `docs/impl/deploy-prod-shadow.md` §2 (vLLM satırı)

## Branch ve worktree

Worktree planner tarafından açılır: `../ais0c-T-085`, `main`'den. Yalnızca orada çalışılır. Ana checkout'ta çalışılmaz, branch değiştirilmez, push yapılmaz (AGENTS.md hard rule 9).

## İzinli dosyalar

- `config/litellm/litellm.prod.yaml`, `config/litellm/litellm.dev.yaml`
- `config/models/registry.prod.yaml`, `config/models/registry.dev.yaml`, `config/models/registry.dev-free.yaml` (yalnızca `prod_equivalent`)
- `deploy/compose/docker-compose.prod.yaml`, `deploy/compose/.env.prod.example`, `deploy/compose/README.md` (yalnızca Qwen'i anan satırlar)
- `tests/deploy/`
- `services/worker/src/ais0c_worker/` ve testleri: yalnızca `VLLM_QWEN_*`'ı adıyla anan yerler varsa

`packages/`, prompt'lar ve `config/agents/` değişmez. `config/litellm/litellm.dev-free.yaml` değişmez (dört alias zaten tek ücretsiz modelde).

## Adımlar

1. **`litellm.prod.yaml`.** `soc-verifier` ve `soc-report`'un `litellm_params`'ı `soc-reasoning`'inkiyle aynı olur: `model: hosted_vllm/deepseek-ai/DeepSeek-V4-Flash`, `api_base: os.environ/VLLM_DEEPSEEK_V4_FLASH_API_BASE`, `api_key: os.environ/VLLM_DEEPSEEK_V4_FLASH_API_KEY`. Dosya başındaki yorum D-45'i söyler (tek model; alias'lar ileride ikinci model için ayrı tutulur). `environment_variables.HOSTED_VLLM_API_BASE` (`.invalid` koruması) aynen kalır.
2. **`registry.prod.yaml`.** `soc-verifier` ve `soc-report` girdileri `soc-reasoning`'inkinin aynısı olur (`target`, `prod_equivalent`, `context_window: 1048576`, `tool_parser`/`reasoning_parser: deepseek_v4`, `artifact: deepseek-ai/DeepSeek-V4-Flash`, null alanlar ve yorumları). Dosya başındaki yorumlarda Qwen geçen cümleler D-45'e göre düzeltilir.
3. **`litellm.dev.yaml` ve `registry.dev.yaml`** (D-12: dev prod'un modellerini kullanır). `soc-verifier` ve `soc-report` OpenRouter'da `soc-reasoning`'in modeline ve sağlayıcı sabitlemesine (`order` tek eleman, `allow_fallbacks: false`, T-103) geçer; registry'de `target`, `prod_equivalent`, `provider`, `parallel_tool_calls`, `forced_tool_choice`, `artifact` `soc-reasoning`'inkiyle aynı. Qwen'e özgü yorumlar (`parallel_tool_calls` 404, `tool_choice` 400) kaldırılır ya da "D-44'te Qwen için geçerliydi" diye geçmişe alınır.
4. **`registry.dev-free.yaml`.** Yalnızca `soc-verifier` ve `soc-report`'un `prod_equivalent`'ı `hosted_vllm/deepseek-ai/DeepSeek-V4-Flash`.
5. **Prod compose ve örnek env.** `litellm` ve `preflight` servislerinden `VLLM_QWEN_122B_API_BASE` ve `VLLM_QWEN_122B_API_KEY` kalkar; `.env.prod.example`'dan iki satır ve yorumları kalkar. `deploy/compose/README.md`'de Qwen'i anan satır varsa D-45'e göre düzeltilir.
6. **Testler.**
   - `test_reasoning_and_verifier_use_different_models` ve `test_shared_model_between_reasoning_and_verifier_is_detected` kaldırılır; yerine `test_prod_config_routes_every_alias_to_deepseek_v4_flash` (prod config'teki dört alias'ın `model`'i `hosted_vllm/deepseek-ai/DeepSeek-V4-Flash`, `api_base` `os.environ/VLLM_DEEPSEEK_V4_FLASH_API_BASE`). Farklı model kuralını uygulayan yardımcı fonksiyon testlerde başka yerde kullanılmıyorsa o da kalkar.
   - `test_prod_compose_mentions_no_qwen_variable`: prod compose'ta ve `.env.prod.example`'da `VLLM_QWEN` geçmez.
   - Mevcut `test_dev_registry_runs_the_prod_models`, `test_registry_describes_every_alias_of_its_litellm_config`, `test_free_dev_registry_names_the_prod_equivalents`, `test_env_example_lists_every_variable` yeni değerlerle geçer.

## Kabul kriterleri ve testler

1. Prod'da dört alias DeepSeek V4 Flash'a, aynı ortam değişkenleriyle gider: `test_prod_config_routes_every_alias_to_deepseek_v4_flash`.
2. Prod registry dört alias için DeepSeek V4 Flash'ı anlatır; `model_release verify`'ın testleri (`packages/activities/tests/test_model_release.py`, `tests/deploy/test_model_registry.py`) geçer.
3. Dev (OpenRouter) ve ücretsiz dev registry'si prod eşdeğeri olarak DeepSeek V4 Flash'ı gösterir: mevcut D-12 testleri geçer.
4. Prod compose ve örnek env'de Qwen değişkeni yok: `test_prod_compose_mentions_no_qwen_variable`; `docker compose -f deploy/compose/docker-compose.prod.yaml config` örnek değerlerle geçer (mevcut `test_prod_compose_config_is_valid`).
5. Prod LiteLLM'in dış servise gitmeme koruması aynen: `test_prod_config_fails_closed_without_an_api_base`, `test_prod_config_uses_only_on_prem_vllm` geçer.

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run lint-imports
uv run pytest tests/deploy packages/activities/tests/test_model_release.py services/worker -q
uv run pytest -q                  # tam suite, ~8 dk; PR'dan önce zorunlu
git grep -I -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu|space-bunny|opencode' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

## PR

`../ais0c-prs/PR-T-085.md`, `.github/pull_request_template.md` biçiminde.

## Durma noktaları

- `packages/` altında bir değişiklik gerekiyor (örnek: kod bir alias'ın modelini ya da Qwen'i adıyla biliyor): dur ve PR'da yaz.
- Gerçek model çağrısı gerekiyor: yapma; dur ve PR'da yaz.

## Notlar

- Gerçek model, lab ya da ağ yok. Dev LiteLLM'i yeniden başlatma; planner yapar.
- Effort: düşük-orta (config ve testler).
