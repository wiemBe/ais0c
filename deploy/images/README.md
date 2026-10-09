# Platform imajları

İki imaj vardır; ikisi de repo kökünden build edilir (T-076). Gateway imajı ayrıdır
(`services/mcp-gateway/Dockerfile`).

```bash
docker build -f deploy/images/platform.Dockerfile -t ais0c-platform:dev .
docker build -f deploy/images/ui.Dockerfile -t ais0c-ui:dev .
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

İçindekiler: `/opt/venv` (`uv.lock`'taki sürümler, dev grupları yok), `/app/config/agents`, `/app/config/models`, `/app/prompts`, `/app/skills`. `AIS0C_WORKER_ROOT=/app`. Bir imaj bir release'tir; içerik değişikliği yeni imajdır.

İçinde olmayanlar: sır, `.env`, `deploy/compose/secrets/`, `config/litellm/` (LiteLLM ayrı servistir), testler, dokümanlar. Ortam değişkenleri, `/run/secrets` altındaki token'lar ve `HEALTHCHECK` çalışma anında compose'tan gelir.

## `ais0c-ui`

`apps/ui`'nin build çıktısını servis eden `nginx-unprivileged` (uid 101). Yalnızca `8443/tcp` üzerinde TLS dinler; düz HTTP yoktur. `/api/` istekleri `http://api:8000`'e (compose'taki API servisi) iletilir, diğer yollar `index.html`'e düşer.

TLS dosyaları imajda değildir, çalışma anında bağlanır:

- `/run/secrets/ui-tls.crt` (sertifika, gerekirse zincirle)
- `/run/secrets/ui-tls.key` (özel anahtar)

Yanıtlara `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` ve `Content-Security-Policy: default-src 'self'` başlıkları eklenir.
