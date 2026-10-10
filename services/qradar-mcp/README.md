# qradar-mcp: AI SOC platformu fork'u

Bu repo, IBM'in [IBM/qradar-mcp](https://github.com/IBM/qradar-mcp) projesinin AI SOC platformu için sahiplenilmiş fork'udur (karar T-04). IBM projeyi ürün olarak desteklemediğini ve bakımını yapmayacağını belirtiyor; bakım bu repodadır. Fork noktası: upstream `main`, commit `f51c007` (2026-09-29).

Platform bu sunucuyu doğrudan değil, MCP Policy Gateway'in arkasında kullanır. Ajanlar ne bu sunucuya ne de QRadar'a erişir; araç allowlist'i, AQL Guard, alan filtresi ve kanıt kaydı gateway'dedir. Bu fork'un işi, gateway'e dar ve öngörülebilir bir araç yüzeyi sunmaktır.

Upstream'in kendi belgeleri bu bölümün altında, "Upstream README" başlığından itibaren değiştirilmeden durur. O belgeler upstream'in giriş noktalarını (`server.py`, `stdio_server.py`, `config.json`) anlatır; platform imajı bunları kullanmaz.

## Upstream'den farkı

| Konu | Upstream | Bu fork |
|---|---|---|
| Giriş noktası | `server.py`, `stdio_server.py` | `qradar-mcp-fork --profile <profil>`; imajın `ENTRYPOINT`'i |
| Kayıtlı araçlar | `feature_toggles.json`'a göre 83 araca kadar | Profil başına izin listesi: `qradar-read` 22 araç, `qradar-note` 2 araç |
| QRadar kimlik bilgisi | İstemcinin her istekte gönderdiği `SEC`/`QRadarCSRF` ya da `config.json` | Sunucunun kendi token'ı; ortam değişkeninden veya secret dosyasından |
| Sunucuya erişim | İstekteki QRadar token'ı | `MCP_AUTH_TOKEN` ile `Authorization: Bearer`; yalnızca gateway bilir |
| API sürümü | `Version` header'ı gönderilmez | Açılışta `/api/help/versions`; bilinen sürümlerin en yükseği her isteğe sabitlenir |
| Çıktı | Metin; bazı araçlarda tablo veya rapor | JSON; MCP structured content ve araç başına çıktı şeması |
| Girdi | Bilinmeyen argümanlar yok sayılır | JSON Schema ile doğrulama; bilinmeyen argüman reddedilir |
| Ariel aramaları | Her arama silinebilir; sonuçlar varsayılan olarak bütünüyle gelir | Yalnızca sunucunun açtığı aramalar silinir; sonuçlar her zaman `Range` header'ıyla sayfalı |
| Loglar | Token'lar için özel bir koruma yok | Secret değerleri bütün log çıktısında `[REDACTED]` olur |
| Kaynaklar (MCP resources) | AQL alan, fonksiyon ve rehber kaynakları | Kayıtlı değil |

### Kod nerede

| Yol | İçerik |
|---|---|
| `fork/` | Platform giriş noktası, alt sınıflar ve sarmalayıcılar. T-040'ın doğrudan değiştirdiği upstream araç dosyaları aşağıda listelenir. |
| `fork/profiles/*.json` | Profil dosyaları; biçimi upstream `feature_toggles.json` ile aynı |
| `snapshots/` | Profil başına araç listesi ile her aracın girdi ve çıktı şeması; `fork/schema_export.py` üretir |
| `tests/fork/` | Birim testleri; QRadar yerine süreç içi sahte bir API (`tests/fork/fake_qradar.py`) |
| `tests/lab/` | Lab QRadar'a karşı contract testleri |
| `UPSTREAM_SYNC.md` | Upstream inceleme kaydı |

Değiştirilen upstream yapılandırma ve doküman dosyaları: `Dockerfile`, `docker-compose.yml`, `pyproject.toml`, `.dockerignore`, `.gitignore`, `README.md` (bu bölüm) ve `NOTICE`.

T-040 ayrıca üç upstream Python araç dosyasını değiştirdi:

- `tools/offense/add_offense_note.py`: `note_text`, query string yerine UTF-8 form gövdesiyle gönderilir.
- `tools/reference_data/get_reference_table.py`: `filter` argümanı şemadan ve QRadar isteğinden kaldırıldı.
- `tools/reference_data/get_reference_map.py`: `filter` argümanı şemadan ve QRadar isteğinden kaldırıldı.

Bu değişikliklere karşılık gelen upstream testleri de T-040'ta uyarlandı: `tests/tools/offense/test_add_offense_note.py`, `tests/tools/reference_data/test_get_reference_table.py` ve `tests/tools/reference_data/test_get_reference_map.py`.

## Profiller

Sunucu tek bir profille açılır. Okuma ve not yazma iki ayrı instance'ta, iki ayrı QRadar token'ıyla çalışır (architecture §11.2).

| Profil | Araçlar |
|---|---|
| `qradar-read` | `get_offense`, `list_offenses`, `list_offense_types`, `list_offense_closing_reasons`, `list_source_addresses`, `list_local_destination_addresses`, `list_rules`, `get_rule`, `list_assets`, `list_log_sources`, `get_log_source`, `list_log_source_types`, `list_reference_sets`, `get_reference_set`, `list_reference_maps`, `get_reference_map`, `list_reference_tables`, `get_reference_table`, `create_ariel_search`, `get_ariel_search_status`, `get_ariel_search_results`, `delete_ariel_search` |
| `qradar-note` | `add_offense_note`, `get_offense_notes` |

Profil dosyalarında `verb_toggles` ve `group_toggles` boştur; upstream'in `FeatureToggleManager`'ı bu durumda hiçbir aracı açmaz, yalnızca `per_tool_toggles`'ta adı geçen sınıfları açar. Dosyalar tek güvence değildir. Sunucu açılırken seçilen araçlar koddaki kurallarla da denetlenir (`fork/tool_profiles.py`); bir kural bozulursa sunucu açılmaz:

- Offense'i değiştiren araçlar (`set_offense_status`, `assign_offense`, `set_offense_follow_up`, `set_offense_protected`) hiçbir profilde kayıtlı olamaz (D-19).
- `qradar-read` yalnızca HTTP GET araçlarını ve Ariel arama oluşturma ile silmeyi içerebilir.
- `qradar-note` yalnızca not ekleme ve not okumayı içerebilir.
- Profil dosyası upstream'de olmayan bir araç sınıfına atıf yaparsa sunucu açılmaz. Böylece upstream'de yeniden adlandırılan bir araç sessizce kaybolmaz.

Bir profile araç eklemek veya çıkarmak bir sözleşme değişikliğidir: profil dosyası, `fork/tool_specs.py`'deki çıktı şeması, `tests/lab/test_contract.py`'deki lab çağrısı ve `snapshots/` birlikte değişir. ais0c reposundaki `config/connectors/qradar.yaml` de güncellenir.

## Çalıştırma

| Değişken | Zorunlu | Varsayılan | Açıklama |
|---|---|---|---|
| `QRADAR_CONSOLE_FQDN` | evet | | Konsol adı: `qradar.example.com`, `qradar.example.com:8443` veya `https://qradar.example.com`. API yalnızca HTTPS ile çağrılır. |
| `QRADAR_AUTH_TOKEN` veya `QRADAR_AUTH_TOKEN_FILE` | biri | | QRadar authorized service token'ı. `_FILE` bir Docker secret dosyasını gösterir. İkisi birden verilirse sunucu açılmaz. |
| `MCP_AUTH_TOKEN` veya `MCP_AUTH_TOKEN_FILE` | biri | | Gateway'in `Authorization: Bearer` ile gönderdiği token; en az 32 karakter, QRadar token'ından farklı. Örnek: `openssl rand -hex 32` |
| `QRADAR_API_VERSIONS` | hayır | `27.0,29.0` | Bilinen API sürümleri. QRadar'ın sunduğu en yüksek bilinen sürüm seçilir. |
| `QRADAR_VERIFY_SSL` | hayır | `true` | TLS sertifika doğrulaması. Yalnızca lab'da kapatın. |
| `REQUESTS_CA_BUNDLE` | hayır | | Kurum CA dosyasının yolu |
| `MCP_HTTPX_TIMEOUT` | hayır | `30` | QRadar isteği zaman aşımı (saniye) |
| `LOG_LEVEL` | hayır | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` veya `CRITICAL` |

Açılış sırası fail-closed'dur. Profil bilinmiyorsa çıkış kodu 2'dir. Ayar eksik veya çelişkiliyse, QRadar'a ulaşılamıyorsa ya da QRadar bilinen sürümlerin hiçbirini sunmuyorsa çıkış kodu 1'dir.

```bash
docker build -t qradar-mcp-fork:local .
docker run --rm -p 127.0.0.1:5000:5000 \
  -e QRADAR_CONSOLE_FQDN=qradar.example.com \
  -e QRADAR_AUTH_TOKEN_FILE=/run/secrets/qradar_token \
  -v "$PWD/secrets/qradar_read_token:/run/secrets/qradar_token:ro" \
  -e MCP_AUTH_TOKEN_FILE=/run/secrets/mcp_token \
  -v "$PWD/secrets/mcp_read_token:/run/secrets/mcp_token:ro" \
  qradar-mcp-fork:local --profile qradar-read
```

`docker-compose.yml` iki instance'ı (`qradar-mcp-read`, `qradar-mcp-note`) birlikte açar. Secret dosyalarını `secrets/` altına koyun (git dışıdır). Container 1001 numaralı kullanıcıyla çalıştığı için dosyalar bu kullanıcı tarafından okunabilmelidir.

| Uç nokta | Açıklama |
|---|---|
| `POST /mcp` | MCP, streamable HTTP. Yanıtlar JSON'dır ve oturum tutulmaz (stateless). Bearer token gerekir. |
| `GET /healthz` | `{"status": "ok"}`; token gerekmez. |

Ariel arama sahipliği süreç belleğinde tutulur. Instance başına tek bir worker süreci çalıştırın. Sunucu yeniden başlarsa önceki aramalarını silemez; o aramaların QRadar'daki saklama süreleri dolunca kendiliğinden silinirler.

## Testler ve sözleşme snapshot'ları

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r dev_requirements.txt
uv pip install --python .venv/bin/python --no-deps -e .
.venv/bin/python -m pytest                                  # upstream ve fork testleri
.venv/bin/python -m qradar_mcp.fork.schema_export --check   # snapshot'lar güncel mi
uvx ruff@0.16.10 check && uvx ruff@0.16.10 format --check   # yalnızca fork kodu
```

Bir araç şeması bilerek değiştiyse `python -m qradar_mcp.fork.schema_export` ile `snapshots/` yeniden üretilir ve fark PR'da incelenir. Snapshot güncellenmezse CI başarısız olur.

Lab contract testleri her profilin her aracını lab QRadar'da örnek girdiyle çağırır ve çıktıyı snapshot'taki şemayla doğrular. Lab ortamı tanımlı değilse bu testler atlanır:

```bash
QRADAR_LAB_URL=qradar-lab.example.com QRADAR_LAB_TOKEN=... \
  .venv/bin/python -m pytest tests/lab -p no:xdist
```

İsteğe bağlı değişkenler şunlardır: `QRADAR_LAB_VERIFY_SSL` (varsayılan `true`), `REQUESTS_CA_BUNDLE` ve `QRADAR_LAB_OFFENSE_ID`. Sonuncusu not araçlarının yazacağı offense'tir; verilmezse bulunan ilk offense kullanılır ve o offense'e bir test notu eklenir. Çıktının sonunda lab QRadar'ın sürümü ve seçilen API sürümü yazılır; bu bilgi PR'a eklenir.

## Upstream ile senkronizasyon

Upstream değişiklikleri **ayda bir** incelenir ve **hiçbir zaman otomatik merge edilmez**. GitHub'ın "Sync fork" düğmesi kullanılmaz; upstream'i bu repoya merge eden bir bot veya zamanlanmış iş yoktur. Upstream'de güvenlik düzeltmesi çıkarsa aylık takvim beklenmez, aynı adımlar hemen uygulanır.

1. Upstream'i alın ve farkı görün:

   ```bash
   git remote add upstream https://github.com/IBM/qradar-mcp.git   # bir kez
   git fetch upstream
   git log --oneline main..upstream/main
   git diff --stat main...upstream/main
   ```

2. Farkı şu sırayla inceleyin:
   - Güvenlik düzeltmeleri ve bağımlılık değişiklikleri (`requirements.txt`, `pyproject.toml`).
   - Profillerdeki araçların dosyaları: `tools/offense/`, `tools/ariel/`, `tools/analytics/get_rule.py` ve `list_rules.py`, `tools/asset/list_assets.py`, `tools/log_source/`, `tools/reference_data/` altındaki okuma araçları.
   - Fork'un bağlandığı upstream dosyaları: `client/qradar_rest_client.py`, `tools/base.py`, `tools/__init__.py`, `tools/schema.py`, `utils/feature_toggle_manager.py`, `utils/mcp_logger.py`, `tools/offense/add_offense_note.py`, `tools/reference_data/get_reference_table.py`, `tools/reference_data/get_reference_map.py`. Son üç dosya T-040'ta doğrudan değiştirildi; upstream değişiklikleri bu dosyalara elle birleştirilir ve karşılık gelen testler birlikte incelenir.
   - Yeni araçlar. Profillerde kendiliğinden açılmazlar; bir profile girmeleri gerekiyorsa ayrı bir PR'da eklenirler.
   - Fork'un sahiplendiği dosyalar (`Dockerfile`, `docker-compose.yml`, `README.md`): Upstream değişikliği gerekiyorsa elle taşınır.

3. Ayrı bir branch'te merge edin ve çakışmaları çözün:

   ```bash
   git switch -c sync/upstream-YYYY-MM main
   git merge --no-ff upstream/main
   ```

4. Doğrulayın: `pytest`, `python -m qradar_mcp.fork.schema_export --check` ve lab contract testleri. Snapshot'lar değiştiyse farkı inceleyin. Bu bir sözleşme değişikliğidir; ais0c reposundaki `config/connectors/qradar.yaml` güncellenir ve gateway tarafına bildirilir.

5. PR açın. PR'ı yazandan farklı bir model ailesi inceler, merge'i insan yapar. Aynı PR'da `UPSTREAM_SYNC.md`'ye bir satır ekleyin: tarih, incelenen upstream commit'i ve karar. Merge edilecek bir şey olmayan aylarda da "incelendi, değişiklik yok" satırı yazılır.

---

# Upstream README

> Below is the upstream README, unchanged. It describes upstream entry points, not the platform image.

# IBM QRadar MCP Server - Official

An open-source Model Context Protocol (MCP) server implementation for IBM QRadar SIEM that enables AI agents to interact with QRadar SIEM data through standardized tools and protocols.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

## Overview

The QRadar MCP Server provides AI agents with standardized access to IBM QRadar SIEM capabilities, including offenses, events and flows, reference data, assets, analytics, configuration, and other security context.

The server can be deployed standalone using Docker or run locally for development. When used with IBM QRadar Investigation Assistant (QIA), the MCP Server can also be exposed directly through the QIA application using QRadar App Framework namespaces, enabling external MCP-compatible clients to connect without requiring deployment of a separate QRadar MCP application.

This enables integrations with MCP-compatible AI platforms and agents while keeping QRadar access and MCP capabilities within the QIA deployment model..

## Project Structure

```
qradar-mcp/
├── client/            # QRadar REST API client
├── tools/             # MCP tools
├── resources/         # MCP resources
├── utils/             # Utilities (auth, logging, validation)
├── tests/             # Comprehensive test suite
├── server.py          # HTTP server entry point (Docker / uvicorn)
├── stdio_server.py    # stdio transport entry point (WxO local toolkit, Claude Desktop)
└── Dockerfile         # Container configuration
```

## Features

- **FastMCP Framework**: Modern, async-first MCP server implementation with uvicorn (ASGI)
- **MCP Protocol Compliance**: Full implementation of Model Context Protocol specification
- **83 Tools**: Comprehensive QRadar API coverage across read and write operations
  - Offense Management (12 tools) - List, retrieve, close, assign, and annotate offenses
  - Reference Data (19 tools) - Create, query, update, and delete reference sets, maps, and tables
  - Data Classification (13 tools) - Manage DSM event mappings, QID records, and categories
  - Ariel Search (8 tools) - Execute AQL queries, poll status, retrieve results, and manage saved searches
  - Config Management (9 tools) - Network hierarchy, staged networks, deploy, and user management
  - Analytics (6 tools) - Retrieve rules, building blocks, and custom actions
  - Log Sources (3 tools) - Query log source configurations and types
  - Network Services (5 tools) - DNS lookup, WHOIS lookup, and IP geolocation
  - Asset Management (2 tools) - List assets and properties
  - Forensics (2 tools) - Query forensics cases
  - QVM (2 tools) - Vulnerability and asset data
  - System Administration (2 tools) - System info and server listing
- **Dynamic Resources**: AQL field definitions, functions, generation guide, and API query syntax reference
- **Dual Authentication**: Supports both user sessions and authorized service tokens

## Deployment

The QRadar MCP Server can be deployed in multiple ways depending on your needs.

### Prerequisites

- Docker 20.10+ and Docker Compose 2.0+ (for containerized deployment)
- Python 3.11+ (for local development)
- Access to a QRadar SIEM deployment
- QRadar SIEM authentication tokens (SEC/CSRF or Authorized Service token)

### Option 1: Docker Compose (Recommended)

The easiest way to deploy the MCP server is using Docker Compose. The server can be run in two modes:
* **Local Single User Mode**: Utilizes `config.json` on the disk to authenticate all incoming requests (useful for local development).
* **Multi User Mode (App Mode)**: Does not use or mount `config.json`. Every client request must supply its own QRadar credentials via headers (`SEC` and `QRadarCSRF`, or Authorized service token as `SEC`).

#### Setup for Local Single User Mode:
1. **Clone the repository and navigate to the directory:**
   ```bash
   git clone https://github.com/IBM/qradar-mcp.git
   cd qradar-mcp
   ```

2. **Create configuration file:**
   ```bash
   cp config.example.json config.json
   # Edit config.json with your QRadar credentials
   ```

3. **Set environment variables:**
   Create a `.env` file:
   ```bash
   cat > .env << EOF
   QRADAR_HOST=your-qradar-host.com
   LOG_LEVEL=info
   EOF
   ```

4. **Start the server:**
   ```bash
   docker-compose up -d
   ```

5. **View logs:**
   ```bash
   docker-compose logs -f qradar-mcp
   ```

6. **Stop the server:**
   ```bash
   docker-compose down
   ```

The server will be available at `http://localhost:5001` (mapped from internal port 5000).

#### Setup for Multi User Mode:
To run the server in multi user mode where no `config.json` is present or mounted on the container.

1. **Clone the repository and navigate to the directory:**
   ```bash
   git clone https://github.com/IBM/qradar-mcp.git
   cd qradar-mcp
   ```

2. **Set environment variables and disable the volume mount:**
   Create a `.env` file:
   ```bash
   cat > .env << EOF
   QRADAR_HOST=your-qradar-host.com
   LOG_LEVEL=info
   EOF
   ```
   Modify `docker-compose.yml` to remove or comment out the `config.json` volume mount block under `volumes`:
   ```yaml
   # - ./config.json:/opt/app-root/qradar-mcp/config.json:ro
   ```

3. **Configure SSL Verification via `REQUESTS_CA_BUNDLE`:**
   In multi-user production deployments, secure SSL/TLS communication with QRadar is highly recommended. To enable SSL certificate verification, set the `REQUESTS_CA_BUNDLE` environment variable in your `.env` file to point to the path of your trusted CA certificate file/bundle inside the container, or pass it via the system environment.
   ```bash
   echo "REQUESTS_CA_BUNDLE=/path/to/your/ca-bundle.crt" >> .env
   ```

4. **Start the server:**
   ```bash
   docker-compose up -d
   ```

### Option 2: Manual Docker Build

For more control over the Docker deployment:

1. **Build the image:**
   ```bash
   docker build -t qradar-mcp:latest .
   ```

2. **Run the container in Local Single User Mode:**
   ```bash
   docker run -d \
     --name qradar-mcp-server \
     -p 5001:5000 \
     -e LOG_LEVEL=info \
     -v $(pwd)/config.json:/opt/app-root/config.json:ro \
     -v $(pwd)/logs:/opt/app-root/logs \
     qradar-mcp:latest
   ```
   *Note: In this mode, the container mounts `config.json` to authenticate all requests using those credentials.*

3. **Run the container in Multi User Mode:**
   ```bash
   docker run -d \
     --name qradar-mcp-server \
     -p 5001:5000 \
     --env-file .env \
     -v $(pwd)/logs:/opt/app-root/logs \
     qradar-mcp:latest
   ```
   *Note: In App Mode, every client request must supply its own user session or service credentials in the HTTP request headers (`SEC` and/or `QRadarCSRF`). Environment variables (including `QRADAR_CONSOLE_FQDN` and `REQUESTS_CA_BUNDLE`) are loaded from the `.env` file created in the setup steps above.*

4. **Check status:**
   ```bash
   docker ps
   docker logs qradar-mcp-server
   ```

### Option 3: Run Local with Python

For local development with Python without Docker:

1. **Create virtual environment (recommended):**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. **Install dependencies:**
   ```bash
   pip install -e .
   ```

3. **Configure authentication for local single user mode:**
   ```bash
   cp config.example.json config.json
   # Edit config.json with your QRadar credentials

   # Copy config to parent directory (required for local mode)
   cp config.json ../config.json
   ```
   
   **Note**: Moving or copying `config.json` to the parent directory (`../config.json`) tells the application to run in **Local Mode**. In Local Mode, the client falls back to the credentials configured in `config.json` for requests that do not supply their own credentials. **Should not be done in production or shared multi user environments.**

4. **Run the server:**
   ```bash
   python server.py
   ```

The server will start at `http://localhost:5000`. The port can be modified in server.py if port conflicts occur.

### Verify Deployment

Use the provided test script to verify your deployment:

```bash
# Run the connection test
python tests/local_mcp_connection.py
```

This script will:
1. Load authentication from your `config.json`
2. Connect to the MCP server at `http://localhost:5001`
3. Initialize the MCP session
4. List all available tools
5. Display the first 10 tools

Expected output:
```
QRadar MCP Server - Local Container Test
==================================================
Endpoint: http://localhost:5001/mcp
Auth: Using authorized service token from config.json
...
✅ Found 32 tools
==================================================
✅ MCP Server is fully operational in local mode!
==================================================
```

## Configuration

### Environment Variables

- `QRADAR_HOST`: QRadar instance hostname
- `QRADAR_SEC_TOKEN`: QRadar SEC token (for user sessions)
- `QRADAR_CSRF_TOKEN`: QRadar CSRF token (for user sessions)
- `QRADAR_AUTH_TOKEN`: Authorized service token (alternative to SEC/CSRF)

### Configuration Files

- `config.json`: Main configuration (not committed)
- `config.example.json`: Configuration template
- `mcp_settings.json`: MCP-specific settings (not committed)
- `mcp_settings.example.json`: Settings template

## Security

- **Never commit `config.json` or `mcp_settings.json`** - They contain sensitive tokens.
- **Deployment modes**: Only use the `config.json` files for local single user development. In multi user or production deployments, do not place or mount `config.json` in the expected lookup paths. This ensures the server runs in secure multi user mode, where all API requests are verified using the user's/service's own request context headers.
- **SSL Certificate Verification**: In production, always configure SSL validation by pointing the `REQUESTS_CA_BUNDLE` environment variable to the path of your trusted CA certificates bundle file (e.g., `/etc/ssl/certs/ca-certificates.crt`). Disabling SSL verification is insecure and should only be done for experimentation or local development.
- Tokens are session-based and expire - refresh as needed.
- All endpoints require authentication.
- Supports both user sessions and authorized service tokens.

## Troubleshooting

### Common Issues

- **Authentication errors (401)**: Refresh your QRadar tokens
- **Connection refused**: Verify QRadar host is accessible
- **SSL errors**: Set `verify_ssl: false` for testing
- **Tool not found**: Ensure MCP server is properly initialized

## IBM QRadar Investigation Assistant

[IBM QRadar Investigation Assistant](https://www.ibm.com/docs/en/qradar-common?topic=apps-qradar-investigation-assistant-app) uses this QRadar SIEM MCP server to accelerate your SOC operations - out of the box.

Download the IBM QRadar Investigation Assistant application extension from the IBM Application Exchange [here](https://apps.xforce.ibmcloud.com/extension/53ef188132188ec5682759efdcf23e9a)

## Documentation

- [watsonx Orchestrate Integration Guide](docs/QRADAR_MCP_WXO_INTEGRATION_GUIDE.md) - Full reference covering all three integration patterns (QIA embedded MCP, standalone external server, and WxO local MCP toolkit), network diagrams, environment variable reference, and troubleshooting.

## Community

- **Issues**: Report bugs or request features via [GitHub Issues](https://github.com/IBM/qradar-mcp/issues)

## License

Copyright 2026 IBM Corporation

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

## IBM Public Repository Disclosure

All content in these repositories including code has been provided by IBM under the associated open source software license and IBM is under no obligation to provide enhancements, updates, or support. IBM developers produced this code as an open source project (not as an IBM product), and IBM makes no assertions as to the level of quality nor security, and will not be maintaining this code going forward.
