# Geliştirme ortamı (Docker Compose)

Temporal, PostgreSQL + pgvector, LiteLLM, OpenTelemetry collector ve Mailpit e-posta yakalayıcısını tek komutla ayağa kaldırır ([T-003](../../docs/impl/tasks/T-003-dev-compose.md), mimari §4 ve §25). Yalnızca dev ve lab içindir; prod compose dosyası Faz 1'de yazılır.

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
| `mailpit` | Mailpit 1.31.4 | `127.0.0.1:1025` (SMTP), http://127.0.0.1:8025 (arayüz ve API) | Executor'ın gönderdiği e-postaları yakalar, hiçbir yere iletmez ([T-020](../../docs/impl/tasks/T-020-executor-eposta.md)) |

QRadar'a giden servisler (`mcp-gateway`, `qradar-mcp-read`, `qradar-mcp-note`) `qradar` compose profilindedir ve yalnızca istenince başlar: [QRadar ve gateway](#qradar-ve-gateway-qradar-profili).

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
- **E-posta:** Executor'ı Mailpit'e yönlendir: `AIS0C_SMTP_HOST=127.0.0.1`, `AIS0C_SMTP_PORT=1025`, `AIS0C_SMTP_TLS=none`, `AIS0C_SMTP_FROM=ai-soc@example.com` (değişkenlerin tamamı: `ais0c_activities.email.load_smtp_settings`). Gelen e-postalar http://127.0.0.1:8025 adresinde görünür. Mailpit e-postaları `/tmp` altındaki geçici bir veritabanında tutar; konteyner durunca silinirler.

## QRadar ve gateway (`qradar` profili)

[T-018](../../docs/impl/tasks/T-018-gateway-compose-not-profili.md), mimari §11.2, §13.4 ve §25. Ajanlar ve Action Executor QRadar'a yalnızca MCP Policy Gateway üzerinden ulaşır. QRadar token'ları yalnızca MCP instance'larındadır ve MCP instance'larına yalnızca gateway erişir.

| Servis | İmaj | Adres | Görev |
|---|---|---|---|
| `mcp-gateway` | `ais0c-mcp-gateway:dev`, bu checkout'tan derlenir ([Dockerfile](../../services/mcp-gateway/Dockerfile)) | `127.0.0.1:8090` | MCP Policy Gateway |
| `qradar-mcp-read` | `qradar-mcp-fork:<server_version>` | yalnızca `mcp` ağı | Fork, `--profile qradar-read`; salt okunur QRadar token'ı |
| `qradar-mcp-note` | `qradar-mcp-fork:<server_version>` | yalnızca `mcp` ağı | Fork, `--profile qradar-note`; not ekleyebilen QRadar token'ı, üzerinde yalnızca not araçları kayıtlı |

Ağlar:

- `mcp`: Gateway ve iki MCP instance'ı bu ağdadır, başka servis yoktur. Ağ `internal` olduğu için dışarı çıkışı yoktur. MCP sunucuları `--host` ile yalnızca bu ağdaki adlarına (`qradar-mcp-read.mcp`, `qradar-mcp-note.mcp`) bağlanır; başka bir ağdan gelen bağlantıyı kabul etmez.
- `qradar-egress`: MCP instance'larının QRadar'a çıkışı. Bu ağda başka servis yoktur.
- `qradar-vmnet`: Yalnızca lab override'ında, yalnızca MCP instance'ları için ([Lab QRadar](#lab-qradar)).

### Fork imajı

İmaj fork reposundan (T-006), connector manifest'teki commit'ten ([`server_version`](../../config/connectors/qradar.yaml)) derlenir ve aynı commit ile etiketlenir. Compose bu imajı hiçbir zaman indirmez (`pull_policy: never`); imaj yoksa servis başlamaz. `git archive` yalnızca commit'teki dosyaları gönderir, çalışma dizinindeki değişiklikler imaja girmez.

```bash
FORK=../qradar-mcp   # fork reposunun yolu
VERSION=$(sed -n 's/^server_version: //p' config/connectors/qradar.yaml)
git -C "$FORK" archive --format=tar "$VERSION" | docker build -t "qradar-mcp-fork:$VERSION" -
```

`server_version` değişince `docker-compose.dev.yaml`'daki etiket de değişir; `services/mcp-gateway/tests/test_deploy.py` ikisinin aynı olduğunu kontrol eder.

### Secret dosyaları

Token'lar compose dosyasında değil, `deploy/compose/secrets/` altındaki dosyalardadır. Dizin git dışıdır. Dosyaları [`make_secrets.py`](make_secrets.py) üretir; var olan bir dosyayı değiştirmez, bir token'ı yenilemek için dosyasını silip yeniden çalıştır.

| Dosya | İçerik | Compose'da bağlandığı servis | Compose dışında okuyan |
|---|---|---|---|
| `agents/gateway-token-<profil>` | Ajan profilinin gateway token'ı | `mcp-gateway` | Worker'lar |
| `executor/gateway-token-qradar-note-write` | `qradar-note-write` profilinin token'ı | `mcp-gateway` | Yalnızca Action Executor |
| `mcp/mcp-token-<instance>` | Gateway'in MCP instance'ına sunduğu token | `mcp-gateway` ve o instance | |
| `qradar/qradar-token-read`, `qradar/qradar-token-note` | QRadar authorized service token'ı | Yalnızca ilgili MCP instance'ı | |

Gateway ve MCP token'ları rastgele üretilir. QRadar token'ları QRadar'dan gelir; betik onları `AIS0C_QRADAR_READ_TOKEN` ve `AIS0C_QRADAR_NOTE_TOKEN` değişkenlerinden kopyalar. Okuma token'ı yalnızca okuyabilmeli, not token'ı not ekleyebilmelidir (mimari §11.2). Tek token'ı olan bir lab ikisi için aynı token'ı kullanabilir; betik bunu bir notla belirtir.

```bash
# Lab: token'ı repoya yazmadan, örneğin ~/.config/ais0c/lab.env'den yükle
set -a; . ~/.config/ais0c/lab.env; set +a
AIS0C_QRADAR_READ_TOKEN="$QRADAR_LAB_TOKEN" AIS0C_QRADAR_NOTE_TOKEN="$QRADAR_LAB_TOKEN" \
  uv run python deploy/compose/make_secrets.py
```

Dizinler `0700`, dosyalar `0644` izinlidir: Konteynerler dosyaları başka kullanıcılarla okur (fork 1001, gateway 10001), makinedeki diğer kullanıcılar ise dizine giremez. SELinux'un açık olduğu makinede compose'un dosya secret'ları `container_file_t` etiketini ister; betik `chcon -R -t container_file_t deploy/compose/secrets` çalıştırır, olmazsa komutu yazar.

### Başlatma

1. `.env`'e `QRADAR_CONSOLE_FQDN`'i (QRadar konsolunun adı veya IP'si) ekle. Lab'ın sertifikası kendinden imzalıysa `QRADAR_VERIFY_SSL=false`. Fork, QRadar'a açılışta ulaşamazsa çalışmaz ve yeniden başlar.
2. Fork imajını derle ve secret dosyalarını üret (yukarıda).
3. Yığını profille başlat. Lab'da override dosyası da verilir:

   ```bash
   docker compose -f deploy/compose/docker-compose.dev.yaml \
     -f deploy/compose/docker-compose.lab.yaml --profile qradar up -d --wait
   ```

4. Gateway her çağrıyı uygulama veritabanındaki ajan çalışmasına bağlar. Şemayı bir kez son sürüme taşı:

   ```bash
   AIS0C_DATABASE_URL="postgresql+psycopg://ais0c:${AIS0C_DB_PASSWORD}@127.0.0.1:5432/ais0c" \
     uv run alembic -c packages/storage/alembic.ini upgrade head
   ```

Bu makinede çalışan worker gateway'e `AIS0C_GATEWAY_URL=http://127.0.0.1:8090` ve `AIS0C_WORKER_SECRETS_DIR=deploy/compose/secrets/agents` ile bağlanır. Ariel sorgularının `START`/`STOP` sınırları saat diliminden bağımsız epoch milisaniye olarak üretilir (T-55).

Action Executor yalnızca `deploy/compose/secrets/executor` dizinini alır; not token'ı hiçbir ajan worker'ına verilmez. Gateway de bu profili yalnızca `action-executor` sahte ajanının çalışmalarına açar.

### Batch worker (katalog senkronu)

Analiz Kataloğu senkronunu çalıştıran worker ayrı bir süreçtir ([T-037](../../docs/impl/tasks/T-037-knowledge-sync-worker.md)): `uv run python -m ais0c_worker batch`. Argümansız `python -m ais0c_worker` case worker'ıdır.

| Değişken | Anlamı | Varsayılan |
|---|---|---|
| `AIS0C_DATABASE_URL` | Uygulama veritabanı | yok |
| `AIS0C_GATEWAY_URL` | MCP Policy Gateway | yok |
| `AIS0C_WORKER_SECRETS_DIR` | `gateway-token-qradar-inventory-read` dosyasının bulunduğu dizin | `/run/secrets` |
| `TEMPORAL_ADDRESS` | Temporal frontend | `127.0.0.1:7233` |
| `TEMPORAL_NAMESPACE` | Namespace | `default` |
| `AIS0C_KNOWLEDGE_SYNC_SCHEDULE` | `off` Schedule'a dokunmaz, örneğin ikinci bir batch worker'da | `on` |

Envanter token'ı `make_secrets.py` üretir (`deploy/compose/secrets/agents/gateway-token-qradar-inventory-read`) ve gateway'e verilir; worker aynı dosyayı okur. Gateway `qradar-inventory-read` profilini sunmuyorsa veya token yoksa worker açılışta `RuntimeConfigError` ile durur. Senkron model çağırmadığı için model registry'sine ve LiteLLM'e gerek yoktur.

```bash
set -a; . deploy/compose/.env; set +a
AIS0C_DATABASE_URL="postgresql+psycopg://ais0c:${AIS0C_DB_PASSWORD}@127.0.0.1:5432/ais0c" \
AIS0C_GATEWAY_URL=http://127.0.0.1:8090 \
AIS0C_WORKER_SECRETS_DIR=deploy/compose/secrets/agents \
  uv run python -m ais0c_worker batch
```

Açılışta `knowledge-sync` Schedule'ını kurar: her gün 03:00 Europe/Istanbul'da `soc-batch` kuyruğunda bir `KnowledgeSync` başlatır. Aynı Schedule'ı elle tetiklemek `POST /catalog/sync`'in (T-028) yapacağı gibidir; süren bir koşunun üstüne ikinci bir koşu başlatmaz. `Ctrl-C` veya `SIGTERM` ile düzgünce durur; yarım kalan işi bir sonraki worker tarihinden devam ettirir.

### API (arayüz servisi)

Analist arayüzünün konuştuğu FastAPI servisi ([T-028](../../docs/impl/tasks/T-028-api.md), [api.md](../../docs/impl/api.md)): `uv run python -m ais0c_api`. Prod'da compose servisi T-031'in konusudur; dev'de host'ta çalışır. Yalnızca uygulama veritabanını okur ve operatörün değiştirdiği satırları yazar; QRadar'a, gateway'e veya modele hiçbir istek göndermez.

| Değişken | Anlamı | Varsayılan |
|---|---|---|
| `AIS0C_DATABASE_URL` | Uygulama veritabanı | yok |
| `AIS0C_API_AUTH` | Kimlik doğrulama modu; bugün yalnızca `dev` (T-035 `oidc`'yi ekler) | yok, ayar olmadan başlamaz |
| `AIS0C_API_DEV_USERS_FILE` | `dev` modunun okuduğu kullanıcı dosyası | `dev` modunda yok |
| `AIS0C_API_HOST` | Dinleme adresi | `127.0.0.1` |
| `AIS0C_API_PORT` | Dinleme portu | `8000` |
| `TEMPORAL_ADDRESS` | Temporal frontend; yalnızca `POST /catalog/sync` için | `127.0.0.1:7233` |
| `TEMPORAL_NAMESPACE` | Namespace | `default` |

`AIS0C_API_AUTH` verilmezse veya bilinmeyen bir değerse servis açılmaz (çıkış kodu 2). `dev` modu geliştirme içindir ve açılışta uyarı olarak loglanır.

**Dev kullanıcı dosyası.** `deploy/compose/secrets/api/dev-users.json`, git dışıdır (`deploy/compose/secrets/.gitignore`). Dosyada token'ın kendisi değil **sha256'ı** durur; API gelen token'ın sha256'sını hesaplayıp karşılaştırır. Token'ı üretip hash'ini dosyaya yaz:

```bash
TOKEN=$(openssl rand -hex 32)
HASH=$(printf '%s' "$TOKEN" | sha256sum | cut -d' ' -f1)
mkdir -p deploy/compose/secrets/api
cat > deploy/compose/secrets/api/dev-users.json <<EOF
{
  "users": [
    {
      "token_sha256": "$HASH",
      "subject": "soc-operator-1",
      "display_name": "SOC Operatör 1",
      "roles": ["admin"]
    }
  ]
}
EOF
chmod 600 deploy/compose/secrets/api/dev-users.json
echo "token: $TOKEN"
```

Roller kapsayıcıdır: `admin` ⊇ `hunter` ⊇ `operator`. Dosyada token'ın kendisi hiçbir zaman bulunmaz; hash'i olan tek şey budur.

Aramak için `Authorization: Bearer <token>` başlığı:

```bash
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/me
```

Arayüzün OpenAPI şeması `services/api/openapi.json`'dur; `uv run python -m ais0c_api.openapi services/api/openapi.json` ile yeniden üretilir ve T-029 arayüz tiplerini buradan alır. Çalışan servis şemayı sunmaz (`/openapi.json` ve `/docs` yoktur).

### Executor worker (QRadar notu ve e-posta)

Action Executor'ın iki activity'si (`write_offense_note`, `send_email`) kendi sürecinde ve `soc-executor` kuyruğunda çalışır ([T-045](../../docs/impl/tasks/T-045-executor-vaka-akisi.md), T-33 (1)): `uv run python -m ais0c_worker executor`. Dev'de host'ta çalışır. Ajan token'ı, model registry'si ve LiteLLM istemez; `deploy/compose/secrets/executor` dizinini ve SMTP ayarlarını yalnızca bu süreç okur.

| Değişken | Anlamı | Varsayılan |
|---|---|---|
| `AIS0C_DATABASE_URL` | Uygulama veritabanı | yok |
| `AIS0C_GATEWAY_URL` | MCP Policy Gateway | yok |
| `AIS0C_EXECUTOR_SECRETS_DIR` | `gateway-token-qradar-note-write` (ve SMTP girişi varsa `smtp-password`) dosyalarının dizini | `/run/secrets` |
| `AIS0C_SMTP_*` | Relay ayarları; dev'de Mailpit (yukarıda, "E-posta") | yok |
| `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE` | Temporal | `127.0.0.1:7233`, `default` |

```bash
set -a; . deploy/compose/.env; set +a
AIS0C_DATABASE_URL="postgresql+psycopg://ais0c:${AIS0C_DB_PASSWORD}@127.0.0.1:5432/ais0c" \
AIS0C_GATEWAY_URL=http://127.0.0.1:8090 \
AIS0C_EXECUTOR_SECRETS_DIR=deploy/compose/secrets/executor \
AIS0C_SMTP_HOST=127.0.0.1 AIS0C_SMTP_PORT=1025 AIS0C_SMTP_TLS=none \
AIS0C_SMTP_FROM=ai-soc@example.com \
  uv run python -m ais0c_worker executor
```

- Gateway `qradar` profiliyle açık olmalıdır: worker açılışta not profilinin araç listesini okur. Token, SMTP ayarı veya gateway yoksa `RuntimeConfigError` ile durur (çıkış kodu 2).
- Notun ve e-postanın vaka linkini case worker kurar; case worker `AIS0C_CASE_URL_BASE` olmadan başlamaz (örnek: `AIS0C_CASE_URL_BASE=http://127.0.0.1:5173/cases`).
- Kill switch kapalıyken (veritabanında bayrak yoksa da kapalıdır) case workflow aynı çağrıları yapar, executor yazmaz; `notes_written` ve `notifications` satırları `disabled` olur (shadow modu, T-23). Executor worker çalışmıyorsa vaka beklemez: çağrılar `soc-executor` kuyruğunda en fazla bir saat bekler, worker o arada açılırsa sırayla yazılır.
- Lab'da not yazılacaksa yalnızca planner'ın verdiği offense'e yazılır; lab offense'i açılmaz ve kapatılmaz.

### Lab QRadar

Lab QRadar, libvirt ağında (`virbr0`) bir VM'dir. libvirt başka köprülerden gelen trafiği reddettiği için compose'un bridge ağlarından, varsayılan ağ dahil, lab QRadar'a ulaşılamaz (ECONNREFUSED). [`docker-compose.lab.yaml`](docker-compose.lab.yaml) QRadar'a giden iki MCP instance'ını dış `qradar-vmnet` ağına bağlar. Bu ağ `virbr0` üzerinde bir macvlan'dır ve konteyneri doğrudan VM'lerin segmentine koyar. Bir kez oluşturulur; değerler libvirt'in varsayılan ağına göredir:

```bash
docker network create -d macvlan -o parent=virbr0 \
  --subnet 192.168.122.0/24 --gateway 192.168.122.1 --ip-range 192.168.122.240/28 qradar-vmnet
```

Gateway ve diğer servisler bu ağa girmez. MCP sunucuları yalnızca `mcp` ağındaki adlarında dinlediği için lab segmentinden onlara bağlanılamaz.

Docker, makine açılırken `virbr0`'dan önce başlarsa macvlan sürücüsü ağı yükleyemez ve konteynerler `network id "..." not found` hatasıyla başlamaz. Docker'ı yeniden başlat (`sudo systemctl restart docker`) ya da ağı silip yukarıdaki komutla yeniden oluştur.

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

  `qradar` profiliyle başlatılmış yığında `COMPOSE_PROFILES=qradar` de ver; gateway ve MCP instance'larının sağlıklı olması da beklenir. Profilin konteynerleri çalışıyorsa bu değişken olmadan da sağlıklı olmaları gerekir.
- `qradar` profilinin statik testleri `services/mcp-gateway/tests/test_deploy.py`'dedir: gateway imajı ve servisi, fork imajının etiketi, token'ların secret dosyalarından okunması, ağ yalıtımı, her secret'ın yalnızca gereken serviste olması, lab override'ı ve `make_secrets.py`.
- E-posta testi yalnızca Mailpit'i kullanır; veritabanını testler kendisi açar (Docker gerekir). Bir uyarı e-postasını gönderir, Mailpit'te konusunu, alıcılarını, başlıklarını ve gövdesini doğrular, sonra test e-postalarını siler:

  ```bash
  docker compose -f deploy/compose/docker-compose.dev.yaml up -d --wait mailpit
  AIS0C_DEV_STACK=1 uv run pytest packages/activities/tests/test_email_dev_stack.py
  ```
- Batch worker'ın testi `qradar` profiliyle başlatılmış yığını kullanır: worker sürecini başlatır, Schedule'ı elle tetikler, koşunun sonucunu bekler ve katalogdaki kural sayısının sonuçtakiyle aynı olduğunu gösterir. Lab'da hiçbir şey açmaz, kapatmaz ve yazmaz; senkronun kendisi tek yazıcıdır.

  ```bash
  set -a; . deploy/compose/.env; set +a
  AIS0C_DATABASE_URL="postgresql+psycopg://ais0c:${AIS0C_DB_PASSWORD}@127.0.0.1:5432/ais0c" \
    AIS0C_DEV_STACK=1 uv run pytest services/worker/tests/test_batch_worker_dev_stack.py -s
  ```

  Worker'ın diğer testleri (`services/worker/tests/test_batch_worker.py`) yığını gerektirmez: kendi PostgreSQL'ini ve Temporal'ının yerel geliştirme sunucusunu açar, gateway'i temsil eden bir HTTP sunucusu kullanır.
- API'nin testleri (`services/api/tests/`) yığını gerektirmez: gerçek PostgreSQL'ini testcontainers ile açar, gerçek uygulamayı bu veritabanının üzerine kurar ve sahte bir Temporal ile `POST /catalog/sync`'i sınar. Model, QRadar veya Temporal sunucusu çağırmaz. `tests/api/` altındaki testler paketler arasıdır: OpenAPI şemasının güncel olduğunu ve iki paketin Schedule kimliğinin aynı olduğunu kontrol ederler.

  ```bash
  uv run pytest services/api/tests tests/api
  ```

  OpenAPI şemasını değiştirdiysen yeniden üret; aksi halde şema testi kırmızı olur:

  ```bash
  uv run python -m ais0c_api.openapi services/api/openapi.json
  ```

## Durdurma ve sıfırlama

- Durdurmak için `docker compose -f deploy/compose/docker-compose.dev.yaml --profile qradar down`. `--profile qradar` olmadan profilin konteynerleri çalışmaya devam eder. Veriler `ais0c-dev_postgres-data` volume'unda kalır.
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
