# Repo Yapısı ve Paket Sınırları

Bu doküman hedef repo yapısını tanımlar. Klasörler ilgili görevler ilerledikçe oluşturulur; burada olmayan bir üst seviye klasör açmak sözleşme değişikliğidir (bkz. [multi-agent-dev.md](multi-agent-dev.md)).

## Dizin yapısı

```text
ais0c/
├── AGENTS.md                  # tüm kodlama ajanlarının ortak kuralları
├── CLAUDE.md                  # AGENTS.md'yi içe aktarır
├── pyproject.toml             # uv workspace kökü, ruff/pyright/pytest/import-linter ayarları
├── docs/                      # mimari, kararlar, harness, impl sözleşmeleri, görevler
├── packages/                  # Python kütüphaneleri (servis değil)
│   ├── contracts/             # Pydantic şemaları; tek doğruluk kaynağı
│   ├── policy/                # ToolIntent doğrulama, AQL Guard, alan filtresi; saf fonksiyonlar
│   ├── querylang/             # Sigma derleme (pySigma), AQL yardımcıları
│   ├── storage/               # Postgres erişimi, Alembic migration'ları
│   ├── knowledge/             # ATT&CK/CTI içeri alma, hunt pack yükleme, Analiz Kataloğu senkronu
│   ├── agents/                # Pydantic AI ajanları, prompt ve manifest yükleme, gateway client
│   ├── activities/            # Temporal activity'leri: zenginleştirme, analitik, gruplama
│   ├── workflows/             # Temporal workflow'ları; deterministik
│   └── executor/              # QRadar notu, e-posta, hunt PDF'i; şablonlar burada
├── services/                  # çalışan süreçler (giriş noktaları)
│   ├── api/                   # FastAPI
│   ├── worker/                # Temporal worker'ları: case, hunt, batch
│   └── mcp-gateway/           # MCP Policy Gateway: MCP Python SDK üzerinde ince proxy (T-18)
├── apps/
│   └── ui/                    # React + Vite + TypeScript
├── config/
│   ├── agents/                # agent manifest'leri (architecture §8.1)
│   ├── connectors/            # connector manifest'leri (§8.2)
│   ├── models/                # model registry: registry.dev.yaml, registry.prod.yaml (§8.4)
│   ├── litellm/               # litellm.dev.yaml, litellm.prod.yaml
│   ├── policies/              # gateway profilleri ve kota havuzları
│   └── sigma/                 # pySigma pipeline'ları (bankanın custom property eşlemeleri)
├── prompts/                   # ajan prompt'ları, sürümlü (prompts.md)
├── skills/                    # onaylı skill'ler: skills/<id>/<sürüm>/{skill.yaml, instructions.md} (architecture §7)
├── hunt-packs/                # onaylı hunt pack YAML'ları (hunt-pack.md)
├── harness/                   # eval suite'leri, senaryolar, fixture'lar, sentetik log üretici
├── deploy/
│   ├── compose/               # docker-compose.dev.yaml, docker-compose.prod.yaml
│   └── images/                # platform (worker'lar + API) ve ui (nginx) imajlarının Dockerfile'ları
└── tests/                     # paketler arası entegrasyon ve contract testleri
```

Fork'lanan `qradar-mcp` bu repoda değil, **ayrı bir repoda** tutulur. Bu repo onu connector manifest'teki sabitlenmiş sürümle (commit veya imaj etiketi) kullanır. Böylece upstream takibi ve platform geliştirmesi birbirine karışmaz.

## Paketlerin sorumlulukları

| Paket | Sorumluluk | İçermez |
|---|---|---|
| `contracts` | Pydantic modelleri, enum'lar, JSON Schema dışa aktarımı ([contracts.md](contracts.md)) | İş mantığı, I/O |
| `policy` | ToolIntent doğrulama, AQL/CQL Guard, profil bazlı alan filtresi, untrusted veri sarmalama | Ağ çağrısı |
| `querylang` | Sigma → AQL/CQL derleme, AQL yardımcıları | QRadar'a bağlantı |
| `storage` | Tablolar, repository fonksiyonları, migration'lar ([data-model.md](data-model.md)) | Ajan veya workflow mantığı |
| `knowledge` | ATT&CK STIX içeri alma, CTI, hunt pack doğrulama, katalog senkronu | LLM çağrısı |
| `agents` | Ajan tanımları, prompt yükleme, gateway client, structured output | QRadar/Falcon client'ı, yazma aracı |
| `activities` | Temporal activity'leri; ajanları ve deterministik işleri çağırır | Workflow mantığı |
| `workflows` | `OffenseIntake`, `CaseWorkflow`, `HuntWorkflow`, `TuningWorkflow`, `KnowledgeSync` | I/O; yalnızca activity çağırır |
| `executor` | QRadar notu, e-posta, PDF üretimi; şablonlar | LLM çağrısı |

## Bağımlılık kuralları

Bir paket yalnızca tablodaki paketleri import edebilir. CI'da import-linter bu kuralları zorlar.

| Paket | Import edebileceği iç paketler |
|---|---|
| `contracts` | Hiçbiri |
| `policy` | `contracts` |
| `querylang` | `contracts` |
| `storage` | `contracts` |
| `knowledge` | `contracts`, `storage`, `querylang` |
| `agents` | `contracts`, `policy` |
| `executor` | `contracts`, `storage`, `policy` |
| `activities` | `contracts`, `storage`, `knowledge`, `querylang`, `policy`, `agents`, `executor` |
| `workflows` | `contracts` (activity'leri isimleriyle çağırır, import etmez) |
| `services/api` | `contracts`, `storage` (hunt başlatmak için Temporal client) |
| `services/worker` | `workflows`, `activities` |
| `services/mcp-gateway` | `contracts`, `policy`, `querylang`, `storage` |
| `apps/ui` | Yalnızca API; tipler OpenAPI'den üretilir |

Ek yasaklar:

- `agents` paketi `mcp` sunucu SDK'sını ve herhangi bir QRadar/Falcon client'ını import edemez. Araçlara yalnızca gateway client'ı üzerinden erişir.
- Sağlayıcı adları (`openai`, `openrouter`, `deepseek`, `anthropic` vb.) `config/litellm/` ve `config/models/` dışında geçemez. CI bunu grep ile kontrol eder. İki istisna vardır: `packages/agents/src/ais0c_agents/llm.py` LiteLLM'e OpenAI uyumlu API ile bağlandığı için OpenAI uyumlu client'ı import eder; `packages/agents/pyproject.toml` da bu client'ı bağımlılık olarak tanımlar. Bu kontrol kod, prompt ve skill dizinlerini (`packages/`, `services/`, `apps/`, `prompts/`, `harness/`, `skills/`) tarar; skill talimatları da prompt'un parçasıdır (T-26). `docs/` taranmaz.

## Araçlar

| İş | Araç |
|---|---|
| Python sürümü | 3.12+ |
| Paket ve workspace yönetimi | uv (workspace) |
| Lint ve format | ruff |
| Tip kontrolü | pyright |
| Test | pytest; Temporal için `temporalio.testing` |
| Paket sınırları | import-linter |
| Migration | Alembic |
| Secret taraması | gitleaks |
| Frontend | pnpm, Vite, React, TypeScript |
| API tipleri | FastAPI'nin OpenAPI çıktısından `openapi-typescript` ile üretilir |
| PDF | WeasyPrint (HTML şablondan) |

## İsimlendirme

- Python dağıtım adları `ais0c-<paket>`, import adları `ais0c_<paket>` biçimindedir (örnek: `ais0c_contracts`). Servislerde de aynı kural geçerlidir: `ais0c_api`, `ais0c_worker`, `ais0c_mcp_gateway`. `harness/` da `ais0c_harness` adıyla bir workspace üyesidir.
- Her Python paketi src düzenini kullanır: kod `packages/<paket>/src/ais0c_<paket>/`, testler `packages/<paket>/tests/` altındadır.
- Lab QRadar'a bağlanan testler `@pytest.mark.lab` ile işaretlenir. `QRADAR_LAB_URL` ve `QRADAR_LAB_TOKEN` ortam değişkenleri yoksa bu testler atlanır. Bu değerler hiçbir zaman repoya yazılmaz.
- Workflow ID'leri: `case-<offense_id>`, `case-hunt-<hunt_id>-<n>`, `hunt-<pack>-<başlangıç>-<bitiş>-<kapsam_hash>`, `group-<grup_id>`.
- Prompt dosyaları: `prompts/<ajan>/v<N>.md`. Ortak kurallar: `prompts/_shared/rules/v<N>.md`; sürümü agent manifest'in `shared_rules` alanı seçer.
- Zaman damgaları veritabanında ve API'de UTC tutulur, arayüzde ve raporlarda Europe/Istanbul saatiyle gösterilir.
