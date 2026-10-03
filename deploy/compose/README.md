# Geliştirme ortamı (Docker Compose)

Temporal, PostgreSQL + pgvector, LiteLLM ve OpenTelemetry collector'ı tek komutla ayağa kaldırır ([T-003](../../docs/impl/tasks/T-003-dev-compose.md), mimari §4 ve §25). Yalnızca dev ve lab içindir; prod compose dosyası Faz 1'de yazılır.

## Servisler

| Servis | İmaj | Adres | Görev |
|---|---|---|---|
| `postgres` | pgvector 0.8.7, PostgreSQL 18 | `127.0.0.1:5432` | `ais0c` (uygulama, `vector` eklentisi kurulu), `temporal` ve `temporal_visibility` veritabanları |
| `temporal-schema` | Temporal admin-tools 1.31.3 | | Temporal tablolarını kurar veya yükseltir, sonra çıkar |
| `temporal` | Temporal server 1.31.3 | `127.0.0.1:7233` (gRPC) | Workflow motoru |
| `temporal-admin-tools` | Temporal admin-tools 1.31.3 | | `default` namespace'ini oluşturur, Temporal CLI için açık kalır |
| `temporal-ui` | Temporal UI 2.54.1 | http://127.0.0.1:8080 | Workflow arayüzü |
| `litellm` | LiteLLM 1.103.2 | `127.0.0.1:4000` | Model gateway, [`litellm.dev.yaml`](../../config/litellm/litellm.dev.yaml) ile |
| `otel-collector` | OTel collector contrib 0.161.0 | `127.0.0.1:4317` (gRPC), `127.0.0.1:4318` (HTTP) | OTLP alır, yalnızca debug exporter'a yazar |

Veritabanı rolleri: `ais0c` yalnızca `ais0c` veritabanına, `temporal` yalnızca kendi iki veritabanına bağlanabilir. İkisi de süper kullanıcı değildir. pgvector "trusted" bir eklenti olmadığı için init script'i onu süper kullanıcıyla kurar; migration'ların çalıştıracağı `CREATE EXTENSION IF NOT EXISTS vector` sorunsuz geçer.

## Başlatma

Komutlar repo kökünden çalıştırılır.

1. Secret dosyasını oluştur ve her değeri doldur. Değerler için rastgele hex kullan (`openssl rand -hex 24`). `.env` git'e girmez.

   ```bash
   cp deploy/compose/.env.example deploy/compose/.env
   ```

2. Yığını başlat. `--wait`, bütün servisler healthcheck'ten geçene kadar bekler; imajlar indirildikten sonra yaklaşık 30 saniye sürer.

   ```bash
   docker compose -f deploy/compose/docker-compose.dev.yaml up -d --wait
   ```

3. Durumu kontrol et. `temporal-schema` `Exited (0)`, diğer servisler `healthy` görünür.

   ```bash
   docker compose -f deploy/compose/docker-compose.dev.yaml ps -a
   ```

## Kullanım

- **Temporal CLI:** `docker compose -f deploy/compose/docker-compose.dev.yaml exec temporal-admin-tools temporal workflow list`
- **Postgres:** `postgresql://ais0c:<AIS0C_DB_PASSWORD>@127.0.0.1:5432/ais0c`
- **LiteLLM:** OpenAI uyumlu API, `http://127.0.0.1:4000/v1`, başlık `Authorization: Bearer <LITELLM_MASTER_KEY>`. Model adı olarak yalnızca alias'lar kullanılır: `soc-fast`, `soc-reasoning`, `soc-verifier`, `soc-report`. Alias'ların hangi modele gittiği [`config/litellm/`](../../config/litellm/), yetenekleri [`config/models/`](../../config/models/) içindedir.
- **OTLP:** `http://127.0.0.1:4318` (HTTP) veya `127.0.0.1:4317` (gRPC). Gelen veriyi görmek için `docker compose -f deploy/compose/docker-compose.dev.yaml logs -f otel-collector`.

## LiteLLM smoke testi

`.env`'de `OPENROUTER_API_KEY` tanımlıysa `soc-fast`'e kısa bir istek atar ve yanıtı yazar. Anahtar yoksa `SKIPPED` yazar ve başarıyla çıkar.

```bash
set -a; . deploy/compose/.env; set +a
uv run python deploy/compose/smoke_litellm.py
```

Anahtarı yığın çalışırken eklediysen önce LiteLLM'i yeniden oluştur: `docker compose -f deploy/compose/docker-compose.dev.yaml up -d litellm`.

## Testler

- `tests/deploy/` altındaki statik testler her `uv run pytest` çalıştırmasında koşar: imaj sabitleme, secret'lar, LiteLLM konfigürasyonları, model registry'leri ve smoke script'i.
- Çalışan yığına karşı testler açıkça istenince koşar. Healthcheck'leri, namespace'i, veritabanlarını, pgvector'ü, LiteLLM'i ve collector'ı kontrol eder; prod LiteLLM konfigürasyonunu da sabitlenmiş LiteLLM imajında, ağsız bir konteynerde yükler. `OPENROUTER_API_KEY` de tanımlıysa smoke testi de koşar.

  ```bash
  AIS0C_DEV_STACK=1 uv run pytest tests/deploy/test_dev_stack.py
  ```

## Durdurma ve sıfırlama

- Durdurmak için `docker compose -f deploy/compose/docker-compose.dev.yaml down`. Veriler `ais0c-dev_postgres-data` volume'unda kalır.
- Sıfırlamak için `down -v`. Init script'i yalnızca boş veri dizininde çalışır; parolaları veya `postgres/init/` altındaki script'i değiştirdiysen yığını sıfırla.

## İmaj güncelleme

Her imaj etiket ve digest ile sabitlenir; `latest` kullanılamaz (`tests/deploy/test_compose_file.py`). Güncellerken ikisini birlikte değiştir. Digest'i şu komutun `Digest:` satırından al:

```bash
docker buildx imagetools inspect docker.io/temporalio/server:<sürüm>
```

Temporal server ve admin-tools aynı sürümde tutulur. LiteLLM imajları cosign ile imzalıdır; yeni bir sürümü sabitlemeden önce imzayı LiteLLM'in sürüm notlarındaki anahtarla doğrula.

## Notlar

- Portlar yalnızca `127.0.0.1`'e açılır, çünkü Temporal'da kimlik doğrulama yoktur. Yığın uzak bir VM'deyse SSH port yönlendirmesi kullan.
- Bind mount'lar salt okunurdur ve SELinux için `z` etiketi taşır (Fedora, RHEL).
- LiteLLM root olmayan bir kullanıcıyla ve salt okunur dosya sistemiyle çalışır. Açılışta model maliyet tablosunu indirmez (`LITELLM_LOCAL_MODEL_COST_MAP`).
- Collector'ın imajında kabuk ve HTTP istemcisi yoktur. Healthcheck, sabitlenmiş busybox imajını salt okunur bir image volume olarak bağlar. Bunun için Docker Engine 29 ve Docker Compose 2.35 veya üzeri gerekir (Engine 29.7.2 ve Compose 5.5.1 ile denendi).
- `litellm.prod.yaml` bu yığında kullanılmaz. LiteLLM, `api_base`'i boş kalan bir `hosted_vllm` modelinin isteğini public OpenAI API'sine gönderir; prod konfigürasyonu bunu `.invalid` bir adrese sabitleyerek engeller. Prod dağıtımında yine de her `VLLM_*_API_BASE` değişkeni zorunlu tutulmalıdır.
