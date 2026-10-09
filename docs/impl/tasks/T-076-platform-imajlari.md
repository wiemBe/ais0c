# T-076: Platform imajları: worker'lar ve API için Python imajı, arayüz için nginx imajı (MVP 1/3)

## Amaç

Prod shadow'un (T-031, MVP) ilk parçası. Bugün yalnızca gateway'in imajı var (`services/mcp-gateway/Dockerfile`); case, batch ve executor worker'ları ile analist API'si host'ta `uv run` ile çalışıyor, arayüz `vite` ile. Prod'da hepsi imajdan çalışacak. Bu görev iki imaj ekler:

1. **`ais0c-platform`**: bütün Python servisleri için tek imaj. Komut servisi seçer: `python -m ais0c_worker` (case), `python -m ais0c_worker batch`, `python -m ais0c_worker executor`, `python -m ais0c_api`. İmaj onaylı içeriği taşır: `config/agents/`, `config/models/`, `prompts/`, `skills/`. Böylece bir imaj bir release'tir; içerik değişikliği yeni imajdır.
2. **`ais0c-ui`**: `apps/ui`'nin build çıktısını servis eden nginx; `/api/` isteklerini API servisine iletir. TLS nginx'te biter.

Compose dosyası T-077'nin, ön kontrol komutu T-078'in işidir.

## Okunacaklar

Yalnızca bunlar:

- `services/mcp-gateway/Dockerfile` ve `Dockerfile.dockerignore` (**izlenecek desen**: iki aşamalı build, `uv sync --locked --no-dev --no-editable --package`, digest'le sabitlenmiş taban imajları, uid 10001, imajda sır ve config yok)
- `packages/activities/src/ais0c_activities/runtime.py:1-40` (worker'ın ortam değişkenleri; `AIS0C_WORKER_ROOT`)
- `services/worker/src/ais0c_worker/__main__.py` ve `services/api/src/ais0c_api/__main__.py` (giriş noktaları)
- `apps/ui/vite.config.ts` (`/api` → API), `apps/ui/src/api/client.ts:60` (`API_PREFIX = "/api/v1"`), `apps/ui/package.json` (`build`)
- `tests/deploy/test_compose_file.py:40-120` (imaj referansı ve `FROM` satırı denetimleri; yeni Dockerfile'lar da bu denetimlerden geçer)
- `docs/impl/repo-structure.md`'de `deploy/` bölümü

## Branch ve worktree

```bash
git worktree add ../ais0c-T-076 -b agent/<araç>/T-076 main
cd ../ais0c-T-076
```

Ana checkout'ta çalışılmaz, branch değiştirilmez, push yapılmaz.

## İzinli dosyalar

- Yeni: `deploy/images/platform.Dockerfile`, `deploy/images/platform.Dockerfile.dockerignore`, `deploy/images/ui.Dockerfile`, `deploy/images/ui.Dockerfile.dockerignore`, `deploy/images/nginx/ui.conf`, `deploy/images/README.md`
- `tests/deploy/` altında yeni `test_images.py`; `test_compose_file.py`'deki `FROM` denetiminin yeni dosyaları da kapsaması için gereken en küçük değişiklik
- `docs/impl/repo-structure.md`: yalnızca `deploy/images/` satırı

`packages/`, `services/`, `apps/ui/src` ve compose dosyaları değişmez.

## Adımlar

1. **`platform.Dockerfile`** (build context: repo kökü). Gateway Dockerfile'ının yapısı:
   - `uv` aşaması ve build aşaması gateway'dekiyle aynı taban imajları ve digest'leri.
   - `RUN uv sync --locked --no-dev --no-editable --package ais0c-worker --package ais0c-api`. İki paket tek venv'e girer; `ais0c_worker` ve `ais0c_api` aynı imajda.
   - Son aşama: `/opt/venv`, ayrıca `COPY config/agents /app/config/agents`, `COPY config/models /app/config/models`, `COPY prompts /app/prompts`, `COPY skills /app/skills`. `config/litellm/` imaja **girmez** (LiteLLM ayrı servistir).
   - `ENV AIS0C_WORKER_ROOT=/app`, `WORKDIR /app`, kullanıcı `ais0c` uid/gid 10001, `USER 10001:10001`.
   - `ENTRYPOINT ["python", "-m"]`, `CMD ["ais0c_worker"]`. Compose komutu `["ais0c_worker", "batch"]`, `["ais0c_api"]` gibi verir.
   - `HEALTHCHECK` yok (servislere göre değişir; compose verir).
   - Başlıkta gateway'deki gibi yorum: ne olduğu, build komutu, imajda sır ve ortam config'i olmadığı.
2. **`platform.Dockerfile.dockerignore`**: gateway'inki gibi `*` ile başlar; yalnızca `.python-version`, `pyproject.toml`, `uv.lock`, her üye `pyproject.toml`, worker ve API'nin bağımlı olduğu paketlerin `src/`'leri (`uv sync`'in istediği; `pyproject.toml`'lardan çıkar), `config/agents`, `config/models`, `prompts`, `skills`. `deploy/compose/.env`, `deploy/compose/secrets/`, `.git`, testler, dokümanlar **girmez**.
3. **`ui.Dockerfile`**:
   - Build aşaması: `node:22` (digest'le), `corepack enable`, `pnpm install --frozen-lockfile`, `pnpm build` (`apps/ui/`).
   - Son aşama: `nginxinc/nginx-unprivileged` alpine (digest'le; root olmadan 8080/8443 dinler). `COPY --from=build /src/apps/ui/dist /usr/share/nginx/html`, `COPY deploy/images/nginx/ui.conf /etc/nginx/conf.d/default.conf`.
   - Digest'ler hafızadan yazılmaz: `docker buildx imagetools inspect <imaj:etiket>` (ya da `docker pull` + `docker inspect --format '{{index .RepoDigests 0}}'`) ile alınır; etiket ve digest `FROM` satırına birlikte yazılır.
4. **`nginx/ui.conf`**:
   - `listen 8443 ssl;` sertifika `/run/secrets/ui-tls.crt`, anahtar `/run/secrets/ui-tls.key`; `ssl_protocols TLSv1.2 TLSv1.3;`. Düz HTTP dinlenmez.
   - `location /api/ { proxy_pass http://api:8000; }` ve `Host`, `X-Forwarded-For`, `X-Forwarded-Proto` başlıkları. Upstream adı `api` (T-077'deki servis adı).
   - `location / { try_files $uri /index.html; }` (SPA).
   - Güvenlik başlıkları: `X-Content-Type-Options nosniff`, `X-Frame-Options DENY`, `Referrer-Policy no-referrer`, `Content-Security-Policy "default-src 'self'"` (build çıktısı bu CSP ile çalışmıyorsa en dar çalışan hali; PR'da gerekçe).
   - `server_tokens off;`
5. **`deploy/images/README.md`** (Türkçe, kısa): iki imaj, build komutları, imajın içindekiler ve içinde olmayanlar (sır, `.env`, LiteLLM config'i), servis başına komut tablosu, TLS dosyaları.
6. **Testler** (`tests/deploy/test_images.py`).

## Kabul kriterleri ve testler

1. **Sabitlenmiş tabanlar:** iki yeni Dockerfile'daki her `FROM` etiket **ve** sha256 digest taşır (`test_compose_file.py`'deki denetim bu dosyaları da kapsar ya da `test_images.py` aynı yardımcıyla denetler). Negatif: `test_unpinned_from_line_is_reported` (digest'siz bir `FROM` satırı yakalanır).
2. **Kök olmadan:** `test_platform_image_runs_as_10001` ve `test_ui_image_runs_unprivileged` (`USER` satırı; nginx için unprivileged taban).
3. **Sır ve ortam dışarıda:** `test_dockerignore_keeps_secrets_out`: iki `.dockerignore` da `*` ile başlar ve `deploy/compose/.env`, `deploy/compose/secrets`, `.git` hiçbir `!` satırıyla geri alınmaz. `test_platform_image_has_no_litellm_config`: `config/litellm` platform imajına kopyalanmaz.
4. **İçerik:** `test_platform_image_carries_the_approved_content`: `config/agents`, `config/models`, `prompts`, `skills` kopyalanır ve `AIS0C_WORKER_ROOT=/app`.
5. **nginx:** `test_nginx_serves_only_tls` (yalnızca `listen 8443 ssl`), `test_nginx_proxies_api_to_the_api_service` (`/api/` → `http://api:8000`), `test_nginx_sends_the_security_headers`.
6. **Gerçek build** (`docker` yoksa atlanır, CI'da koşar): `test_platform_image_builds_and_imports` imajı build eder ve `docker run --rm --entrypoint python <imaj> -c "import ais0c_worker, ais0c_api"` 0 döner; `test_ui_image_builds` build eder. Ağ gerekir; build bir kez yapılır (modül düzeyinde fixture).

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest tests/deploy -q
uv run pytest -q          # tam suite, ~7 dk
uv run lint-imports
git grep -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu|space-bunny|opencode' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
docker build -f deploy/images/platform.Dockerfile -t ais0c-platform:dev .
docker build -f deploy/images/ui.Dockerfile -t ais0c-ui:dev .
```

## PR

`../ais0c-prs/PR-T-076.md`. İki imajın boyutunu ve build süresini yaz.

## Kapsam dışı

- Prod compose (T-077), ön kontrol komutu (T-078), imaj kayıt defteri ve imzalama (dağıtım runbook'u).
- Gateway imajı (değişmez).

## Notlar

- Gerçek model, lab ya da `deploy/compose/.env` gerekmez; okunmaz.
- Docker rootful ve SELinux açık; build için bind mount yok.
- Sözleşme değişikliği, izinli dosya dışı ihtiyaç ya da dokümanla çelişki çıkarsa dur ve PR'da yaz.
