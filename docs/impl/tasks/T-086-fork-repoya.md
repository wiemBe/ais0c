# T-086: qradar-mcp fork'u ais0c reposunun içine (`services/qradar-mcp/`)

## Amaç

Karar D-46 (kullanıcı, 2026-10-10): prod kodu git'le çekecek; fork bugün yalnızca geliştirici makinesinde, remote'suz bir repoda (`/home/efe/Documents/qradar-mcp`, branch `agent/claude-code/integration` = `7f17ada`). Fork ais0c reposunun içine `git subtree` ile girer ve bizim imajlarımızdan biri olur: dev compose onu checkout'tan build eder, prod onu `AIS0C_VERSION` etiketiyle kullanır, release betiği onu diğer dört imaj gibi build eder.

Fork kendi Python projesi olarak kalır (Python 3.11, kendi `requirements.txt`'i ve testleri): uv workspace'e girmez, ais0c paketleri onu import etmez (hard rule 1), ais0c'nin ruff/pyright/pytest koşuları onu taramaz; kendi testleri CI'da ayrı bir işte koşar.

## Okunacaklar

- `docs/decisions.md`: D-46, T-18, T-34, T-117; `docs/impl/repo-structure.md` (fork paragrafı, D-46 ile güncel)
- Fork: `/home/efe/Documents/qradar-mcp` (`README.md` "Platform fork" ve "Upstream ile senkronizasyon", `UPSTREAM_SYNC.md`, `Dockerfile`, `.dockerignore`, `tox.ini`, `requirements.txt`, `dev_requirements.txt`, `tests/`)
- `config/connectors/qradar.yaml:12-20` (`server`, `server_version`)
- `deploy/compose/docker-compose.dev.yaml:24-32` (fork imajı) ve `:225-240` (`mcp-gateway`'in `build:` deseni); `deploy/compose/docker-compose.prod.yaml:36-45` (`x-qradar-mcp`)
- `deploy/compose/README.md:57-80` ("Fork imajı"); `deploy/images/README.md`
- `deploy/release/build_release.py` (`OWN_IMAGES`, `BUILDS`, `--fork`, `_fork_has_commit`, `_build_fork`, `plan_release`'in fork denetimi) ve `tests/deploy/test_release.py`
- `services/mcp-gateway/tests/test_deploy.py` (compose etiketi = `server_version` denetimi)
- `pyproject.toml:8-20` (workspace), `:40-50` (ruff), `:79-100` (pyright, pytest); `.github/workflows/ci.yml` (işler, sağlayıcı grep'i)

## Branch ve worktree

Worktree planner tarafından açılır: `../ais0c-T-086`, `main`'den. Yalnızca orada çalışılır. Ana checkout'ta çalışılmaz, branch değiştirilmez, push yapılmaz (AGENTS.md hard rule 9). Fork reposu (`/home/efe/Documents/qradar-mcp`) yalnızca okunur; orada commit, branch ya da değişiklik yapılmaz.

## İzinli dosyalar

- yeni `services/qradar-mcp/` (subtree'nin getirdiği dosyalar ve yeni `services/qradar-mcp/UPSTREAM`)
- `config/connectors/qradar.yaml`: yalnızca `server_version` ve onu anlatan yorum
- `deploy/compose/docker-compose.dev.yaml`, `deploy/compose/docker-compose.prod.yaml`: yalnızca fork imajı satırları
- `deploy/compose/README.md` ("Fork imajı" ve fork imajını anan satırlar), `deploy/images/README.md`
- `deploy/release/build_release.py`, `tests/deploy/`
- `services/mcp-gateway/tests/test_deploy.py`
- `pyproject.toml`: yalnızca ruff `extend-exclude`, pyright `exclude`, pytest dışlama
- `.github/workflows/ci.yml`: yalnızca fork testlerinin yeni işi

Fork'un kodu (`services/qradar-mcp/` altında subtree'nin getirdiği dosyalar) değişmez; yalnızca `UPSTREAM` eklenir.

## Adımlar

1. **Subtree.** Worktree'de, temiz ağaçta:

   ```bash
   git subtree add --prefix=services/qradar-mcp /home/efe/Documents/qradar-mcp 7f17ada --squash \
     -m "D-46: qradar-mcp fork into services/qradar-mcp (subtree of 7f17ada, squashed)"
   ```

   `7f17ada`, fork'un entegrasyon branch'inin ucu (T-042: `7dcf3ce`'ye yalnızca README/NOTICE ekler). Sonra `git ls-files services/qradar-mcp | wc -l` fork'un `git ls-files | wc -l`'ı (337) ile aynı olmalı; değilse dur.
2. **`services/qradar-mcp/UPSTREAM`** (düz metin, İngilizce):

   ```text
   # Provenance of this directory (D-46). Update both lines on every subtree pull.
   fork_commit: 7f17ada<tam hash>
   upstream: https://github.com/IBM/qradar-mcp f51c007<tam hash>
   ```

   Tam hash'ler fork reposundan (`git -C /home/efe/Documents/qradar-mcp rev-parse 7f17ada`) ve `UPSTREAM_SYNC.md`'nin son satırından (`f51c007`; tam hash'i fork reposunun upstream geçmişinden `git -C … log --format=%H -1 f51c007`) alınır.
3. **`server_version`.** `config/connectors/qradar.yaml`'da `7f17ada`'nın tam hash'i (araç şemaları değişmedi: `7dcf3ce` ile `7f17ada` arasında yalnızca README/NOTICE var). Yorum D-46'yı söyler: alanın anlamı "araç şemalarının alındığı fork commit'i"dir, imaj etiketi değil.
4. **İmaj etiketleri.**
   - Dev: `image: qradar-mcp-fork:dev`, `build: {context: ../../services/qradar-mcp}`, `pull_policy: build` (`mcp-gateway`'in deseni). Yorumdaki "manuel build" talimatı kalkar.
   - Prod: `x-qradar-mcp`'de `image: qradar-mcp-fork:${AIS0C_VERSION:?Set AIS0C_VERSION in .env.prod}`, `pull_policy: never` (diğer kendi imajlarımız gibi).
5. **`test_deploy.py`.** "Compose etiketi = `server_version`" denetimi yerine: dev compose'un fork servisi `services/qradar-mcp`'den build eder; prod'un fork imajı `AIS0C_VERSION` etiketlidir; `services/qradar-mcp/UPSTREAM`'deki `fork_commit` = connector'ın `server_version`'ı (`test_upstream_file_names_the_server_version`).
6. **Release betiği.** `qradar-mcp-fork` `OWN_IMAGES`'a girer; `BUILDS` her imaj için Dockerfile **ve context** taşır (fork: `-f services/qradar-mcp/Dockerfile` ve context `services/qradar-mcp`; diğerleri repo kökü). `--fork`, `DEFAULT_FORK`, `_fork_has_commit`, `_build_fork`, fork commit'i denetimi ve `AIS0C_RELEASE_FORK` kalkar; `RELEASE.md`'de `server_version` satırı "fork commit'i (araç şemaları)" olarak kalır. `--dry-run` çıktısında fork `docker build -f services/qradar-mcp/Dockerfile -t qradar-mcp-fork:<v> services/qradar-mcp` olur. Testler buna göre; gerçek build testi (`AIS0C_RELEASE_TEST=1`) artık fork yolu istemez.
7. **Araçlar.** `pyproject.toml`: ruff `extend-exclude`'a `"services/qradar-mcp"`; pyright `exclude = ["services/qradar-mcp", "**/node_modules", "**/__pycache__", "**/.*"]` (pyright'ın varsayılan dışlamaları `exclude` yazılınca kalktığı için birlikte); pytest `addopts`'a `"--ignore=services/qradar-mcp"`. `uv run ruff check`, `uv run pyright`, `uv run pytest --collect-only -q` fork'a dokunmamalı.
8. **CI.** `.github/workflows/ci.yml`'a yeni iş `qradar-mcp-fork`: Python 3.11, `pip install -r services/qradar-mcp/requirements.txt -r services/qradar-mcp/dev_requirements.txt`, `pytest services/qradar-mcp/tests -q` (çalışma dizini ve bayraklar fork'un `tox.ini`'sindeki gibi). Sağlayıcı grep'i `services`'i zaten kapsar; fork'ta bugün eşleşme yok.
9. **Belgeler.** `deploy/compose/README.md` "Fork imajı": fork artık `services/qradar-mcp/`'de, dev compose `--build` ile build eder, upstream senkronu `git subtree pull --prefix=services/qradar-mcp https://github.com/IBM/qradar-mcp <commit> --squash` ve `UPSTREAM`'in güncellenmesi. `deploy/images/README.md`: beşinci imaj.

## Kabul kriterleri ve testler

1. **İçerik birebir.** `test_the_fork_tree_matches_its_upstream_file` (fork reposu gerekmez): `UPSTREAM`'de iki satır, 40 haneli hash'ler; `services/qradar-mcp/LICENSE` Apache-2.0, `NOTICE` var. PR'a `git diff --stat 7f17ada` eşdeğeri bir karşılaştırma (fork reposundaki `git archive 7f17ada` ile alt dizin arasında `diff -r` boş) yazılır.
2. **ais0c araçları fork'u taramaz.** `test_tooling_excludes_the_fork` (`tests/deploy/`): `pyproject.toml`'da ruff, pyright ve pytest dışlamaları var; `uv run pytest --collect-only -q` çıktısında `services/qradar-mcp` yok (PR'da komut çıktısı).
3. **Compose.** Dev fork servisi checkout'tan build eder, prod fork imajı `AIS0C_VERSION` etiketli ve `pull_policy: never`; `test_prod_images_are_pinned` ve benzerleri güncel haliyle geçer; `docker compose ... config` iki dosyada da geçer.
4. **`server_version` ↔ `UPSTREAM`.** `test_upstream_file_names_the_server_version`.
5. **Release betiği.** `test_dry_run_prints_the_commands_in_order` fork'u yeni context'iyle gösterir; `--fork` yok (`test_the_fork_option_is_gone`: `--fork` verilince argparse hatası, çıkış 2); `test_compose_images_lists_every_prod_image` fork'u `:<version>` ile bekler.
6. **Fork testleri.** Yerelde: `python3.11 -m venv` (scratch dizininde) + iki requirements + `pytest services/qradar-mcp/tests -q` geçer (PR'a sonuç). Python 3.11 yoksa PR'da yazılır; CI işi koşar.
7. **Import sınırı.** `uv run lint-imports` geçer; `git grep -n -E '^(from|import) (fork|tools|utils|client|server)\b' -- packages services/api services/worker services/mcp-gateway harness` boş (ais0c kodu fork modüllerini import etmez).

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run lint-imports
uv run pytest tests/deploy services/mcp-gateway -q
uv run pytest -q                  # tam suite, ~8 dk; PR'dan önce zorunlu
git grep -I -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu|space-bunny|opencode' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
docker compose -f deploy/compose/docker-compose.dev.yaml config >/dev/null
```

Fork imajını bir kez build et: `docker build -t qradar-mcp-fork:t086 services/qradar-mcp`, sonra `docker image rm qradar-mcp-fork:t086`. Dev stack'i (`ais0c-dev`) açma ya da değiştirme.

## PR

`../ais0c-prs/PR-T-086.md`, `.github/pull_request_template.md` biçiminde: subtree komutu, `diff -r` sonucu, kriter başına test adı, kontrollerin sonuçları.

## Durma noktaları

- Subtree'nin dosya sayısı ya da `diff -r` fork'la uyuşmuyor: dur ve PR'da yaz.
- Fork'un kodunu değiştirmen gerekiyor (bir test ya da araç yüzünden): değiştirme; dur ve PR'da yaz.
- `packages/` ya da gateway kodunda değişiklik gerekiyor: dur ve PR'da yaz.

## Notlar

- Lab, gerçek model, push yok. Fork reposuna yazma.
- Birleşince planner dev stack'in `qradar-mcp-*` imajını yeni etiketle kurar ve ana fork reposunun artık kullanılmadığını belgeler.
- Effort: orta (mekanik ama çok dosyalı; release betiği ve testleri dikkat ister).
