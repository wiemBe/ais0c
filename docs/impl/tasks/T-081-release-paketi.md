# T-081: Release paketini üreten betik (MVP)

## Amaç

Prod shadow kurulumu bir release paketiyle yapılır (`docs/impl/deploy-prod-shadow.md` §3, karar T-110 (1), T-113). Bugün paket elle hazırlanıyor: dört imajın ve fork imajının build'i, `docker save`, `gzip`, `sha256sum`, `git archive`, `RELEASE.md`. Elle yapılan adım bir imajı unutabilir (örnek: collector'ın busybox imajı yalnızca bir `type: image` volume'unda geçiyor) ya da kirli bir checkout'tan paket çıkarabilir. Bu görev bunu tek komuta bağlar:

```bash
uv run python deploy/release/build_release.py --version 0.1.0-shadow1 --fork ../qradar-mcp --out ../ais0c-release-0.1.0-shadow1
```

Çıktı dizini:

```text
ais0c-images-0.1.0-shadow1.tar.gz   # docker save: prod compose'un adını verdiği bütün imajlar
ais0c-files-0.1.0-shadow1.tar.gz    # git archive: kurulum dosyaları
SHA256SUMS                          # sha256sum -c ile denetlenir
RELEASE.md                          # sürüm, commit, imaj kimlikleri, dosyaların özeti
```

## Okunacaklar

- `docs/impl/deploy-prod-shadow.md` §3 ve §4 (paketin içeriği ve sunucuda kullanımı)
- `deploy/images/README.md` (dört imajın build komutları; bunlar aynen kullanılır)
- `deploy/compose/README.md:68-78` ("Fork imajı": `git archive` + `docker build`, etiket `server_version`)
- `deploy/compose/docker-compose.prod.yaml` (bütün `image:` satırları ve `otel-collector` ile `preflight`'taki `type: image` volume'larının `source`'u)
- `deploy/compose/make_secrets.py:1-60`, `:125-140` (bu dizindeki betiklerin deseni: modül docstring'i, `argparse`, `main(argv) -> int`, `HERE`/sabitler, `yaml`)
- `tests/deploy/test_prod_compose.py:1-80` (compose dosyasını okuyan yardımcılar ve test deseni)

## Branch ve worktree

Worktree planner tarafından açılır: `../ais0c-T-081`, `main`'den. Yalnızca orada çalışılır. Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz, branch değiştirilmez, push yapılmaz (AGENTS.md hard rule 9).

## İzinli dosyalar

- yeni `deploy/release/build_release.py`
- yeni `tests/deploy/test_release.py`
- `deploy/images/README.md`: sonuna kısa bir "Release paketi" bölümü (komut ve çıktı listesi, Türkçe)

Başka dosya değişmez. Yeni bağımlılık yok (stdlib + PyYAML; `make_secrets.py` gibi).

## Tasarım

```python
VERSION_PATTERN: Final = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$")
OWN_IMAGES: Final = ("ais0c-platform", "ais0c-ui", "ais0c-mcp-gateway", "ais0c-litellm")
BUILDS: Final = {  # image name -> Dockerfile, from deploy/images/README.md
    "ais0c-platform": "deploy/images/platform.Dockerfile",
    "ais0c-ui": "deploy/images/ui.Dockerfile",
    "ais0c-mcp-gateway": "services/mcp-gateway/Dockerfile",
    "ais0c-litellm": "deploy/images/litellm.Dockerfile",
}
ARCHIVED: Final = (  # what goes into ais0c-files-<version>.tar.gz
    "deploy/compose/docker-compose.prod.yaml",
    "deploy/compose/.env.prod.example",
    "deploy/compose/make_secrets.py",
    "deploy/compose/README.md",
    "deploy/compose/postgres",
    "deploy/compose/temporal",
    "deploy/compose/otel-collector",
    "config/connectors/qradar.yaml",   # make_secrets.py reads the profiles from it
    "docs/impl/deploy-prod-shadow.md",
)


@dataclass(frozen=True)
class ReleasePlan:
    version: str
    commit: str               # git rev-parse HEAD
    server_version: str       # config/connectors/qradar.yaml
    images: tuple[str, ...]   # every image the prod compose names, version substituted, unique, sorted


def check_version(value: str) -> str: ...                      # ValueError unless VERSION_PATTERN
def compose_images(compose: Mapping[str, Any], version: str) -> tuple[str, ...]: ...
def working_tree_clean(root: Path) -> bool: ...                # git status --porcelain is empty
def plan_release(root: Path, version: str) -> ReleasePlan: ...
def commands(plan: ReleasePlan, root: Path, fork: Path, out: Path, *, build: bool) -> list[list[str]]: ...
def write_sha256sums(out: Path, names: Sequence[str]) -> Path: ...
def render_release_md(plan: ReleasePlan, image_ids: Mapping[str, str], sums: Mapping[str, str]) -> str: ...
def main(argv: Sequence[str] | None = None) -> int: ...
```

- **`compose_images`**: her servisin `image`'ı ve `volumes` içinde `type: image` olan her girdinin `source`'u. Değerlerdeki `${AIS0C_VERSION:?…}` (ve `${AIS0C_VERSION}`) sürümle değiştirilir; başka bir `${…}` kalırsa `ValueError`. `OWN_IMAGES`'tan her biri tam olarak `<ad>:<version>` olarak bulunmalı; yoksa ya da başka bir etiketle varsa `ValueError`. `qradar-mcp-fork:<server_version>` bulunmalı.
- **`main`**: argümanlar `--version` (zorunlu), `--fork` (varsayılan `../qradar-mcp`), `--out` (zorunlu; yoksa yaratılır, varsa boş olmalı), `--skip-build` (imajlar hazır kabul edilir, yalnızca varlıkları denetlenir), `--dry-run` (komutları sırayla yazdırır, hiçbirini çalıştırmaz, dosya yazmaz). Sıra:
  1. sürüm, temiz çalışma ağacı (izlenmeyen dosya da kirli sayılır), fork'ta `server_version` commit'inin varlığı (`git -C <fork> cat-file -e <sv>^{commit}`), boş çıktı dizini. Biri tutmazsa stderr'e tek satır neden ve **çıkış 2**;
  2. build (`--skip-build` yoksa): dört `docker build -f <Dockerfile> -t <ad>:<version> .` (repo kökünde) ve `git -C <fork> archive --format=tar <sv> | docker build -t qradar-mcp-fork:<sv> -`;
  3. üçüncü parti imajlar yerelde yoksa `docker pull <ref>` (compose'taki ref etiket@digest ile sabittir);
  4. `docker save <images…>` çıktısı Python'da `gzip`'le `ais0c-images-<v>.tar.gz`'ye akıtılır (tamamı belleğe alınmaz);
  5. `git archive --format=tar.gz --prefix=ais0c-<v>/ -o <out>/ais0c-files-<v>.tar.gz HEAD -- <ARCHIVED…>`;
  6. `SHA256SUMS` (iki arşiv, `sha256sum -c` biçimi: `<64 hex>  <ad>`), `RELEASE.md`.
  Bir komut sıfırdan farklı dönerse komut ve çıkış kodu stderr'e yazılır, **çıkış 1**; yarım kalan dosyalar silinmez ama `RELEASE.md` yazılmaz. Başarıda çıkış 0 ve çıktı dizininin yolu.
- **`RELEASE.md`** (Türkçe, düz Markdown): başlık `# ais0c <version>`; sürüm, commit, oluşturma zamanı (UTC, saniye), `server_version`; imaj tablosu (`İmaj`, `Kimlik` = `docker image inspect --format '{{.Id}}'`); dosya tablosu (`Dosya`, `sha256`); kurulumun `ais0c-<v>/docs/impl/deploy-prod-shadow.md` §4'te olduğu cümlesi; boş bir "Gate raporu" bölümü ("Planner doldurur.").
- Sır yok: betik hiçbir ortam değişkenini ya da `deploy/compose/secrets/` altını okumaz, yazdırmaz.

## Kabul kriterleri ve testler

Testler Docker ya da ağ gerektirmez; son kriter hariç.

1. **Sürüm.** `test_version_pattern`: `0.1.0`, `0.1.0-shadow1`, `1.2.3-rc.1` kabul; `latest`, `0.1`, `v0.1.0`, `0.1.0-`, `0.1.0;rm -rf /`, boş dizgi `ValueError`.
2. **İmaj listesi.** `test_compose_images_lists_every_prod_image`: gerçek `docker-compose.prod.yaml` ile `9.9.9-test` için liste dört kendi imajını `:9.9.9-test` ile, `qradar-mcp-fork:<server_version>`'ı, postgres'in ref'ini ve busybox'ın ref'ini (`type: image` volume'dan) içerir; `ais0c-litellm:9.9.9-test` bir kez geçer (hem `image` hem `source`); liste sıralı ve tekildir. `test_an_own_image_with_another_tag_is_refused` (`ais0c-ui:latest` olan sözlük → `ValueError`), `test_an_unresolved_variable_is_refused` (`${OTHER}` → `ValueError`).
3. **Temiz ağaç.** `test_working_tree_clean` geçici bir git deposunda: commit'ten sonra `True`, izlenen dosya değişince `False`, izlenmeyen dosya eklenince `False`. `test_a_dirty_tree_stops_with_exit_2`: `working_tree_clean` `False` dönecek şekilde (monkeypatch) `main([... "--dry-run"])` 2 döner, stderr'de "not clean".
4. **Çıktı dizini.** `test_a_non_empty_out_dir_stops_with_exit_2`.
5. **Arşivin içeriği.** `test_archived_paths_exist_and_hold_no_secret`: `ARCHIVED`'daki her yol repoda var (`git ls-files` ile izleniyor); hiçbiri `deploy/compose/secrets` altında değil, `.env.prod.example` dışında `.env` ile başlayan dosya yok, `docker-compose.dev.yaml` ve `docker-compose.lab.yaml` yok. Bir dizin girdisinin `git ls-files` ile gelen dosyalarında da aynı denetim.
6. **Komutlar.** `test_dry_run_prints_the_commands_in_order`: `--dry-run`, temiz ağaç ve var olan fork commit'i (monkeypatch) ile çıkış 0; yazdırılan komutlar sırayla dört `docker build`, fork'un `git archive … | docker build`, `docker save`, `git archive`; hiçbir dosya yazılmaz. `--skip-build` ile build satırları yoktur.
7. **Sağlama ve RELEASE.md.** `test_sha256sums_pass_sha256sum_check`: geçici dizinde iki dosya, `write_sha256sums`, ardından gerçek `sha256sum -c SHA256SUMS` 0 döner. `test_release_md_names_version_commit_images_and_sums`: sahte verilerle başlık, commit, her imajın satırı ve her dosyanın sağlaması metinde.
8. **Gerçek build** (yalnızca `AIS0C_RELEASE_TEST=1` ile koşar, yoksa atlanır; CI'da atlanır): `test_a_real_release_builds_and_verifies` geçici çıktı dizinine `--version 0.0.0-test` ile paket üretir, `sha256sum -c` geçer, `ais0c-files-…tar.gz` `ARCHIVED`'dakileri `ais0c-0.0.0-test/` önekiyle içerir, `docker load -i` olmadan `tar -tzf ais0c-images-…` `manifest.json` içerir; sonra `docker image rm` ile `0.0.0-test` etiketleri silinir. Fork yolu `AIS0C_RELEASE_FORK` (yoksa test atlanır). Ajan bu testi koşmak zorunda değildir; koşarsa sonucunu PR'a yazar.

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest tests/deploy -q
uv run lint-imports
uv run pytest -q                  # tam suite, ~8 dk; PR'dan önce zorunlu
git grep -I -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu|space-bunny|opencode' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

## PR

`../ais0c-prs/PR-T-081.md`, `.github/pull_request_template.md` biçiminde: kriter başına test adı, `--dry-run` çıktısının bir örneği, kontrollerin sonuçları.

## Durma noktaları

- Prod compose'ta beklenmeyen bir imaj biçimi (örnek: `image` olmayan bir servis, sürüm değişkeninin başka bir adı): dur ve PR'da yaz.
- `deploy/images/README.md`'deki build komutları Dockerfile'larla uyuşmuyor: dur ve PR'da yaz.

## Notlar

- Docker imajı build etmek, `docker pull` ve `docker save` yalnızca kriter 8'in isteğe bağlı testinde gerçekten çalışır. Dev stack'e (`ais0c-dev`) dokunma.
- Sunucuda `make_secrets.py` için `python3` ve PyYAML gerekir; bunu runbook'a planner yazar.
- Effort: orta.
