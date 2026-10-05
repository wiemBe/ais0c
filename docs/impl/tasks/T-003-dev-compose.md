# T-003: Geliştirme ortamı (Docker Compose) ve LiteLLM konfigürasyonu

## Amaç

Tek komutla ayağa kalkan bir geliştirme ortamı kurmak: Temporal, Postgres + pgvector, LiteLLM ve OpenTelemetry collector. LiteLLM için iki konfigürasyon yazılır: dev (OpenRouter) ve prod (on-prem vLLM). Ajanlar her iki ortamda da aynı alias'larla konuşur.

## Okunacaklar

- `docs/architecture.md` §4, §19, §25
- `docs/decisions.md` → D-10, D-11, D-12, D-21, T-03, T-11
- `docs/impl/repo-structure.md` → `config/` dizinleri

## İzinli dizinler

- `deploy/compose/`
- `config/litellm/`
- `config/models/`
- `tests/deploy/`

## Kullanılan sözleşmeler

Yok.

## Kabul kriterleri

1. `docker compose -f deploy/compose/docker-compose.dev.yaml up -d` sonrasında bütün servisler healthcheck'ten geçer. (PR çıktısı)
2. Temporal ve Temporal UI çalışır; `default` namespace'i hazırdır.
3. Postgres'te iki ayrı veritabanı bulunur: `ais0c` ve `temporal`. `ais0c` veritabanında `vector` eklentisi yüklenebilir.
4. `config/litellm/litellm.dev.yaml` şu alias'ları OpenRouter'daki modellere yönlendirir: `soc-fast`, `soc-reasoning`, `soc-verifier`, `soc-report`. Bir smoke script'i `soc-fast`'e kısa bir istek atar ve yanıt alır. Script yalnızca `OPENROUTER_API_KEY` tanımlıysa çalışır.
5. `config/litellm/litellm.prod.yaml` aynı alias'ları on-prem vLLM endpoint'lerine yönlendirir. Bir test, prod konfigürasyonundaki her modelin `hosted_vllm/` önekini kullandığını ve `api_base` değerini ortam değişkeninden aldığını doğrular. Bulut sağlayıcı öneki (`openrouter/`, `openai/`, `deepseek/`, `anthropic/` vb.) bulunursa test başarısız olur.
6. Her iki konfigürasyonda `soc-reasoning` ve `soc-verifier` farklı modellere gider (D-21). Bunu doğrulayan test vardır.
7. `config/models/registry.dev.yaml` ve `registry.prod.yaml`, architecture §8.4'teki biçimde her alias'ın hedefini ve yeteneklerini tanımlar.
8. Bütün imaj etiketleri sabittir; `latest` kullanan imaj bulunursa test başarısız olur.
9. Secret'lar yalnızca ortam değişkeninden gelir. `deploy/compose/.env.example` boş değerlerle commit edilir; `.env` commit edilmez.

## Kapsam dışı

- Prod compose dosyası (Faz 1)
- vLLM kurulumu (prod'da zaten çalışıyor)
- Trace arayüzü seçimi (T-12); bu görevde collector yalnızca debug exporter'a yazar
- `soc-embed` (Faz 0'da gerekmiyor)
- MCP gateway ve qradar-mcp

## Bağımlılıklar

- T-001

## Notlar

- **Model eşlemesi:** Prod modelleri DeepSeek V4 Flash ve Qwen 122B'dir (D-21). Başlangıç önerisi: `soc-reasoning` → DeepSeek V4 Flash, `soc-verifier` → Qwen 122B. Bu bir başlangıç noktasıdır; harness sonuçlarına göre değişir.
- **Dev modelleri:** Dev'de OpenRouter'da aynı modeller kullanılır (D-12). Doğru model ID'lerini OpenRouter katalogundan kontrol et. Aynı model yoksa aynı ailenin en yakın modelini seç ve PR'da belirt.
- **Temporal:** Temporal'ın resmi Docker Compose örneklerini temel al. Temporal kendi veritabanı olarak aynı Postgres'teki `temporal` veritabanını kullanır.
