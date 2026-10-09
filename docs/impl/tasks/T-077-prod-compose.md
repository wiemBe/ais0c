# T-077: Prod shadow compose dosyası (MVP 3/3)

## Amaç

Prod shadow'u (T-031, MVP) tek bir Linux sunucuda Docker Compose ile çalıştıran dosya. Bütün servisler imajdan çalışır (T-076), veritabanı tek seferlik bir `migrate` servisiyle güncellenir (T-078), shadow öncesi `preflight` (T-078) koşar. Shadow hiçbir şey yazmaz ve göndermez (T-23): kill switch yeni veritabanında kapalıdır, açmak iki admin ister (T-033).

## Okunacaklar

Yalnızca bunlar:

- `deploy/compose/docker-compose.dev.yaml` (bütün dosya; **izlenecek desen**: ağ ayrımı `mcp`/`qradar-egress`, `x-qradar-mcp` çapası, sırlar, sağlık denetimleri, `read_only`/`cap_drop`/`no-new-privileges`)
- `deploy/compose/.env.example`
- `config/litellm/litellm.prod.yaml` (okuduğu ortam değişkenleri: `VLLM_*_API_BASE`, `VLLM_*_API_KEY`, `LITELLM_MASTER_KEY`)
- `deploy/images/README.md` (T-076: imajlar ve servis başına komutlar)
- `services/worker/README.md`'de `migrate` ve `preflight` (T-078)
- `deploy/compose/README.md`'de "Executor worker" ve "API" bölümleri (gerekli değişkenler)
- `tests/deploy/test_compose_file.py` (dev dosyasının denetimleri; prod için aynı yardımcılar)

## Branch ve worktree

```bash
git worktree add ../ais0c-T-077 -b agent/<araç>/T-077 main
cd ../ais0c-T-077
```

Ana checkout'ta çalışılmaz, branch değiştirilmez, push yapılmaz.

## İzinli dosyalar

- Yeni: `deploy/compose/docker-compose.prod.yaml`, `deploy/compose/.env.prod.example`, `tests/deploy/test_prod_compose.py`
- `deploy/compose/README.md`: yeni "Prod (shadow)" bölümü
- `tests/deploy/test_compose_file.py`: yalnızca ortak yardımcıları paylaşmak için gereken en küçük değişiklik

`docker-compose.dev.yaml`, `config/`, `packages/`, `services/` değişmez.

## Adımlar

1. **Servisler** (`docker-compose.prod.yaml`, dev dosyasından bağımsız; `profiles` yok):

   | Servis | İmaj | Komut / not |
   |---|---|---|
   | `postgres` | dev'dekiyle aynı (digest'li) | veri `postgres-data` volume'unda |
   | `temporal-schema`, `temporal` | dev'dekilerle aynı | |
   | `temporal-ui` | dev'dekiyle aynı | yalnızca `127.0.0.1:8233` (operasyon) |
   | `otel-collector` | dev'dekiyle aynı | |
   | `litellm` | dev'dekiyle aynı | `config/litellm/litellm.prod.yaml` salt okunur (`ro,z`); dışarı port açılmaz |
   | `mcp-gateway` | `ais0c-mcp-gateway:${AIS0C_VERSION:?}` | `pull_policy: never` |
   | `qradar-mcp-read`, `qradar-mcp-note` | dev'deki fork imajı (aynı commit etiketi) | `QRADAR_CONSOLE_FQDN: ${QRADAR_CONSOLE_FQDN:?}`; lab macvlan'ı yok, `qradar-egress` normal bridge |
   | `migrate` | `ais0c-platform:${AIS0C_VERSION:?}` | `["ais0c_worker", "migrate"]`, `restart: "no"` |
   | `case-worker` | aynı | `["ais0c_worker"]` |
   | `batch-worker` | aynı | `["ais0c_worker", "batch"]` |
   | `executor-worker` | aynı | `["ais0c_worker", "executor"]` |
   | `api` | aynı | `["ais0c_api"]`, `AIS0C_API_HOST: 0.0.0.0` (yalnızca iç ağ) |
   | `ui` | `ais0c-ui:${AIS0C_VERSION:?}` | `"${AIS0C_UI_BIND:-0.0.0.0}:8443:8443"`, sırlar `ui-tls.crt`, `ui-tls.key` |

   Mailpit yok. Kendi imajlarımızda `build:` yok, `pull_policy: never` (imajlar release tar'ından `docker load` ile gelir; runbook).

2. **Sabit ortam değerleri** (compose'da yazılı, `.env`'den değiştirilemez): `AIS0C_SKILLS_MODE: prod`, `AIS0C_MODEL_REGISTRY: config/models/registry.prod.yaml`, `LITELLM_BASE_URL: http://litellm:4000`, `AIS0C_GATEWAY_URL: http://mcp-gateway:8080`, `TEMPORAL_ADDRESS: temporal:7233`.

3. **Bağımlılıklar:** `case-worker`, `batch-worker`, `executor-worker` ve `api`, `migrate`'e `condition: service_completed_successfully` ile bağlanır; worker'lar `temporal` ve `mcp-gateway`'e `service_healthy` ile.

4. **Sırlar:** compose `secrets:`'ın her girdisi `file: ${AIS0C_SECRETS_DIR:?}/<ad>`. Adlar dev'dekiyle aynı (gateway ve MCP token'ları, QRadar token'ları), ek olarak `api-dev-users.json` ve `ui-tls.crt`, `ui-tls.key`. Executor yalnızca `gateway-token-qradar-note-write` (ve varsa `smtp-password`) alır; case worker not token'ını **almaz** (dev'deki ayrım).

5. **Güvenlik:** kendi servislerimiz ve fork'ta `read_only: true`, `cap_drop: [ALL]`, `security_opt: ["no-new-privileges:true"]`, `tmpfs: [/tmp]`, `restart: unless-stopped` (`migrate` hariç). Dışarı açılan tek port `ui`'nin 8443'ü; `temporal-ui` 127.0.0.1'de.

6. **Değişkenler** (`.env.prod.example`, değerler boş, her birinin üstünde Türkçe tek satır açıklama): `AIS0C_VERSION`, `AIS0C_SECRETS_DIR`, `POSTGRES_PASSWORD`, `AIS0C_DB_PASSWORD`, `TEMPORAL_DB_PASSWORD`, `LITELLM_MASTER_KEY`, `VLLM_*` (litellm.prod.yaml'ın okuduğu her değişken), `QRADAR_CONSOLE_FQDN`, `QRADAR_VERIFY_SSL`, `AIS0C_SMTP_HOST`, `AIS0C_SMTP_PORT`, `AIS0C_SMTP_TLS`, `AIS0C_SMTP_FROM`, `AIS0C_CASE_URL_BASE`, `AIS0C_QRADAR_OFFENSE_URL_TEMPLATE`, `AIS0C_API_AUTH`, `AIS0C_UI_BIND`, `AIS0C_ALARM_SYSLOG_HOST`/`PORT`/`PROTOCOL` (H-8). Sır olanlar compose'da varsayılansız (`${X:?}` ya da `${X:-}`; dev'deki `secret_problems` kuralı).

7. **README "Prod (shadow)" bölümü** (Türkçe): sırayla `docker load`, `.env` ve sırlar dizini, `docker compose -f docker-compose.prod.yaml up -d`, `docker compose run --rm case-worker preflight` (ya da `exec`), kill switch'in kapalı kaldığı, API'nin bugün yalnızca `dev` kimlik doğrulamasıyla çalıştığı (OIDC T-035; kullanıcı dosyası sırlar dizininde, token'ların sha256'sı).

## Kabul kriterleri ve testler

`tests/deploy/test_prod_compose.py`:

1. `test_prod_images_are_pinned`: üçüncü taraf imajlar etiket ve digest'li; kendi imajlarımız `${AIS0C_VERSION:?}` etiketli, `build:` yok, `pull_policy: never`. Negatif: `test_own_image_with_latest_is_reported`.
2. `test_prod_fixes_skills_mode_and_registry`: üç worker'da `AIS0C_SKILLS_MODE: prod` ve `AIS0C_MODEL_REGISTRY: config/models/registry.prod.yaml` sabit; `test_dev_skills_mode_in_prod_is_reported` (negatif).
3. `test_only_the_ui_port_is_published`: dışarı açılan port yalnızca `ui:8443`, `temporal-ui` yalnızca `127.0.0.1`. Negatif: `test_published_api_port_is_reported`.
4. `test_prod_secrets_come_only_from_files_or_the_environment`: dev'deki `secret_problems` prod dosyasında boş; sır dosyaları `${AIS0C_SECRETS_DIR:?}` altında.
5. `test_case_worker_gets_no_note_token` ve `test_executor_gets_only_the_note_token`.
6. `test_workers_wait_for_migrate`: dört servis `migrate`'e `service_completed_successfully` ile bağlı; `migrate`'in `restart`'ı `"no"`.
7. `test_hardening_on_every_own_service`: `read_only`, `cap_drop: [ALL]`, `no-new-privileges`.
8. `test_prod_compose_config_is_valid`: `.env.prod.example`'ın her değişkenine sahte değer verilerek `docker compose -f docker-compose.prod.yaml config --quiet` 0 döner (`docker` yoksa atlanır); `test_env_example_lists_every_variable` (compose'daki her `${X…}` örnek dosyada).

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest tests/deploy -q
uv run pytest -q          # tam suite, ~7 dk
uv run lint-imports
git grep -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu|space-bunny|opencode' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

## PR

`../ais0c-prs/PR-T-077.md`. Mümkünse imajlar yerelde build edilip (T-076'nın komutları) prod compose'u sahte değerlerle `up` edilir ve `migrate`'in geçtiği, `ui`'nin 8443'te cevap verdiği gösterilir; QRadar ve vLLM olmadığı için worker'lar sağlıksız kalır, bu beklenir.

## Kapsam dışı

- Runbook (planner yazar), OIDC (T-035), canary'nin yazma kuralları, yedekleme.

## Bağımlılıklar

- T-076 ve T-078 `main`'de.

## Notlar

- Gerçek sır, prod adresi ya da banka bilgisi yazılmaz; örnek dosyada değerler boştur, testlerde RFC 5737 adresleri ve `example.com`.
- Sözleşme değişikliği, izinli dosya dışı ihtiyaç ya da dokümanla çelişki çıkarsa dur ve PR'da yaz.
