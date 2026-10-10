# Platform imajları

Dört imaj vardır; hepsi repo kökünden build edilir (T-076, T-077). Gateway ve LiteLLM imajları ayrı Dockerfile'lardır.

```bash
docker build -f deploy/images/platform.Dockerfile -t ais0c-platform:dev .
docker build -f deploy/images/ui.Dockerfile -t ais0c-ui:dev .
docker build -f services/mcp-gateway/Dockerfile -t ais0c-mcp-gateway:dev .
docker build -f deploy/images/litellm.Dockerfile -t ais0c-litellm:dev .
```

Taban imajlar etiket ve digest ile sabitlenmiştir; `tests/deploy/test_images.py` her `FROM` satırını denetler.

## `ais0c-platform`

Bütün Python servisleri için tek imaj (uid/gid 10001, `ENTRYPOINT ["python", "-m"]`). Compose komutu servisi seçer:

| Servis | Komut |
|---|---|
| Case worker | `["ais0c_worker"]` (varsayılan) |
| Batch worker | `["ais0c_worker", "batch"]` |
| Executor worker | `["ais0c_worker", "executor"]` |
| Analist API | `["ais0c_api"]` |
| Migrate (tek seferlik) | `["ais0c_worker", "migrate"]` |
| Preflight (tek seferlik) | `["ais0c_worker", "preflight"]` |

İçindekiler: `/opt/venv` (`uv.lock`'taki sürümler, dev grupları yok), `/app/config/agents`, `/app/config/models`, `/app/config/policies`, `/app/config/sigma`, `/app/config/telemetry`, `/app/prompts`, `/app/skills`. `AIS0C_WORKER_ROOT=/app`. Bir imaj bir release'tir; içerik değişikliği yeni imajdır.

İçinde olmayanlar: sır, `.env`, `deploy/compose/secrets/`, `config/litellm/` (LiteLLM ayrı imajdır), testler, dokümanlar. Ortam değişkenleri, `/run/secrets` altındaki token'lar ve `HEALTHCHECK` çalışma anında compose'tan gelir.

## `ais0c-ui`

`apps/ui`'nin build çıktısını servis eden `nginx-unprivileged` (uid 101). Yalnızca `8443/tcp` üzerinde TLS dinler; düz HTTP yoktur. `/api/` istekleri `http://api:8000`'e (compose'taki API servisi) iletilir, diğer yollar `index.html`'e düşer.

TLS dosyaları imajda değildir, çalışma anında bağlanır:

- `/run/secrets/ui-tls.crt` (sertifika, gerekirse zincirle)
- `/run/secrets/ui-tls.key` (özel anahtar)

Yanıtlara `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` ve `Content-Security-Policy: default-src 'self'` başlıkları eklenir.

## `ais0c-mcp-gateway`

`services/mcp-gateway/Dockerfile` (uid 10001). Release'in `config/connectors` ve `config/policies` dizinlerini `/etc/ais0c/connectors` ve `/etc/ais0c/policies` altına taşır (`AIS0C_GATEWAY_CONFIG_DIR=/etc/ais0c`). Dev compose aynı yollara checkout'taki dosyaları bağlayarak bunların üzerine yazar. Sır yoktur.

## `ais0c-litellm`

`deploy/images/litellm.Dockerfile`: dev compose'taki LiteLLM imajı (aynı etiket ve digest) artı `config/litellm/litellm.prod.yaml` (`/etc/litellm/litellm.prod.yaml`). Başka değişiklik yoktur. Dosya vLLM adreslerini ve anahtarlarını `os.environ/VLLM_*` olarak adlandırır; imajda adres ya da anahtar yoktur. Prod compose bu dosyayı `preflight` servisine de imajdan salt okunur image volume olarak verir.

## Release paketi

Temiz bir çalışma ağacında prod shadow release paketini repo kökünden üretmek için:

```bash
uv run python deploy/release/build_release.py --version 0.1.0-shadow1 --fork ../qradar-mcp --out ../ais0c-release-0.1.0-shadow1
```

Çıktı dizininde imaj arşivi (`ais0c-images-<sürüm>.tar.gz`), kurulum dosyaları
(`ais0c-files-<sürüm>.tar.gz`), `SHA256SUMS` ve `RELEASE.md` oluşur. Komutları çalıştırmadan
görmek için `--dry-run`, hazır imajları yalnızca denetleyip paketlemek için `--skip-build`
kullanılır.
