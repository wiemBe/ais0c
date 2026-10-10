# T-083: `deploy/compose/README.md`'nin prod kurulum sırası release paketine göre

## Amaç

T-081 release paketini üreten betiği getirdi; planner paketi gerçek bir kurulumla denedi (karar T-117) ve runbook'u (`docs/impl/deploy-prod-shadow.md` §2–§4) güncelledi. `deploy/compose/README.md`'nin "Prod (shadow)" → "Kurulum sırası" bölümü eski kaldı:

- arşiv adı yanlış (`ais0c-<sürüm>.tar`; doğrusu `ais0c-images-<sürüm>.tar.gz`), `SHA256SUMS` denetimi ve dosya arşivinin açılması yok;
- yüklemeden sonra imajların adıyla bulunup bulunmadığı denetlenmiyor (klasik Docker imaj deposunda `etiket@digest` imajları bulunamaz);
- `up -d`'de `--pull never` yok (sunucu internete çıkmaz);
- sır betiği `uv run python …` ile çağrılıyor (sunucuda `uv` yok, `python3` ve PyYAML var);
- "Notlar"daki Docker sürümü "Engine 29" diyor; gereken Engine 28.0+ ve Compose 2.35.0+ ile containerd imaj deposu (T-113, T-117).

Bu görev yalnızca bu metinleri runbook'la aynı hale getirir. Kod değişmez.

## Okunacaklar

- `docs/impl/deploy-prod-shadow.md` §2 (ilk satır, Docker şartı), §3, §4 (bütün bölüm): metnin kaynağı
- `deploy/compose/README.md:385-430` ("Kurulum sırası" ve "Notlar")

## Branch ve worktree

Worktree planner tarafından açılır: `../ais0c-T-083`, `main`'den. Yalnızca orada çalışılır. Ana checkout'ta çalışılmaz, branch değiştirilmez, push yapılmaz (AGENTS.md hard rule 9).

## İzinli dosyalar

- `deploy/compose/README.md`: yalnızca "Kurulum sırası"nın 1., 3. ve 4. maddeleri ve "Notlar"daki collector/Docker sürümü maddesi

## Değişiklikler (metin Türkçe, aynen)

1. **Madde 1** şununla değişir:

   ````markdown
   1. **Paket.** Release paketinin dizininde (`deploy/release/build_release.py`'nin çıktısı, [deploy/images](../images/README.md#release-paketi)):

      ```bash
      sha256sum -c SHA256SUMS
      docker load -i ais0c-images-<sürüm>.tar.gz
      tar -xzf ais0c-files-<sürüm>.tar.gz          # dizin ais0c-<sürüm>/
      sed -n '/^## İmajlar/,/^## Dosyalar/p' RELEASE.md | grep -o '^| `[^`]*`' | tr -d '|` ' \
        | xargs -n1 docker image inspect --format '{{.Id}}' >/dev/null && echo images ok
      ```

      Yüklenen imajlar `ais0c-platform`, `ais0c-ui`, `ais0c-mcp-gateway`, `ais0c-litellm`, fork imajı `qradar-mcp-fork:<commit>` ve üçüncü parti imajlardır. Son komut `images ok` yazmazsa bir `etiket@digest` imajı bulunamıyordur: Docker'ın containerd imaj deposu kapalıdır (Notlar). Sonraki adımlar `ais0c-<sürüm>/deploy/compose/` dizininde yapılır.
   ````

2. **Madde 3**'ün komut bloğu şununla değişir (metnin geri kalanı aynı kalır):

   ````markdown
      ```bash
      AIS0C_QRADAR_READ_TOKEN=... AIS0C_QRADAR_NOTE_TOKEN=... \
        python3 make_secrets.py --directory "$AIS0C_SECRETS_DIR"
      ```

      Betik `python3` ve PyYAML ister (RHEL'de `python3-pyyaml`) ve profilleri paketteki `config/connectors/qradar.yaml`'dan okur.
   ````

3. **Madde 4**'ün komut bloğu şununla değişir; altındaki paragraf aynı kalır:

   ````markdown
      ```bash
      docker compose --env-file .env.prod -f docker-compose.prod.yaml up -d --pull never
      ```

      `--pull never`: sunucu internete çıkmaz; eksik bir imaj indirilmeye çalışılmaz, hata verir.
   ````

4. **Notlar**'daki collector maddesinin son cümlesi ("Bunun için Docker Engine 29 ve Docker Compose 2.35 veya üzeri gerekir (Engine 29.7.2 ve Compose 5.5.1 ile denendi).") şununla değişir:

   ````markdown
   Bunun için Docker Engine 28.0 ve Docker Compose 2.35.0 veya üzeri gerekir (Engine 29.7.2 ve Compose 5.5.1 ile denendi). Docker'ın containerd imaj deposu açık olmalıdır (`docker info` → `driver-type io.containerd.snapshotter.v1`; yeni Docker 29 kurulumlarında varsayılan, yükseltilmiş kurulumda `/etc/docker/daemon.json`'da `"features": {"containerd-snapshotter": true}`): klasik depo `docker load`'dan sonra `etiket@digest` imajlarını bulamaz.
   ````

İç içe kod bloklarını yazarken README'deki girinti düzeni korunur (madde içi bloklar üç boşlukla girintili).

## Kabul kriterleri ve testler

1. `git diff main -- deploy/compose/README.md` yalnızca yukarıdaki dört yeri değiştirir.
2. `uv run pytest tests/deploy -q` geçer (README'yi okuyan testler varsa onlar dahil).
3. Markdown'ın görünümü bozulmaz: madde numaraları 1–6 sırayla kalır, kod blokları kapanır (`grep -c '^ *```' deploy/compose/README.md` çift sayıdır).

## Kontroller

```bash
uv run pytest tests/deploy -q
git diff --check
```

## PR

`../ais0c-prs/PR-T-083.md`, `.github/pull_request_template.md` biçiminde.

## Notlar

- Yalnızca metin. Lab'a, modele, ağa ihtiyaç yok. Sır ya da gerçek adres yazılmaz.
- Effort: düşük.
