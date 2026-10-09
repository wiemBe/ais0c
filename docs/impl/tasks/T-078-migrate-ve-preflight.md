# T-078: `ais0c_worker migrate` ve `ais0c_worker preflight`: prod shadow'un ön kontrolleri (MVP 2/3)

## Amaç

Prod shadow'u (T-031, MVP) açmadan önce iki şey gerekiyor:

1. **Migration'ları çalıştıran bir komut.** Bugün migration'ları yalnızca testler ve e2e kodu çalıştırıyor (`ais0c_storage.migrate.upgrade`); prod'da komut satırı yok. T-077'nin compose dosyasında tek seferlik bir `migrate` servisi bu komutu çalıştıracak, worker'lar onun başarıyla bitmesini bekleyecek.
2. **Shadow'un ön koşullarını denetleyen bir komut.** Runbook'taki "shadow başlamadan" maddeleri (H-7, kill switch kapalı, prod skill modu, gateway profilleri, modeller) tek komutla denetlenir. Komut 0 dönmeden shadow başlamaz.

## Okunacaklar

Yalnızca bunlar:

- `packages/storage/src/ais0c_storage/migrate.py` (bütün dosya)
- `packages/storage/src/ais0c_storage/repositories/flags.py` (kill switch bayrağı `writes_enabled`; satır yoksa kapalı)
- `services/worker/src/ais0c_worker/__main__.py` ve `main.py` (alt komutların deseni: `batch`, `executor`)
- `services/worker/src/ais0c_worker/model_release.py:219-300` (`verify`, `main`; H-7'nin denetimi, T-32)
- `packages/activities/src/ais0c_activities/runtime.py:1-120` (ortam değişkenleri, `RuntimeConfigError`, gateway token'larının okunması)
- `pyproject.toml:300-340` (import-linter: `ais0c_worker` yalnızca `ais0c_workflows` ve `ais0c_activities`'i içe aktarır; storage'a erişim activities üzerinden)
- `docs/decisions.md`'de T-23, T-32, T-58 satırları

## Branch ve worktree

```bash
git worktree add ../ais0c-T-078 -b agent/<araç>/T-078 main
cd ../ais0c-T-078
```

Ana checkout'ta çalışılmaz, branch değiştirilmez, push yapılmaz.

## İzinli dosyalar

- Yeni: `packages/activities/src/ais0c_activities/deploy.py` ve `packages/activities/tests/test_deploy.py`
- `services/worker/src/ais0c_worker/__main__.py`, `main.py`, yeni `services/worker/src/ais0c_worker/preflight.py`, `services/worker/tests/`
- `services/worker/README.md` ve `deploy/compose/README.md`: yalnızca iki komutun bölümü

`packages/contracts`, `packages/storage` (yalnızca okunur, değişmez), migration dosyaları ve workflow kodu değişmez.

## Adımlar

1. **`ais0c_activities.deploy`** (storage'a erişen kısım):

   ```python
   def migrate_to_head(database_url: str) -> str:
       """Upgrade the application database to the newest revision; return that revision."""

   def database_revision(database_url: str) -> tuple[str | None, str]:
       """(current revision or None, head revision)."""

   def writes_enabled(database_url: str) -> bool:
       """The kill switch flag (T-23); False when the row does not exist."""
   ```

   `ais0c_storage.migrate.upgrade` ve `flags` deposu kullanılır; yeni SQL yazılmaz.

2. **`python -m ais0c_worker migrate`**: `AIS0C_DATABASE_URL`'i okur, `migrate_to_head` çalıştırır, `migrated to <revision>` yazar, 0 döner. Ayar yoksa `RuntimeConfigError` mesajıyla 2, veritabanı hatasında 1. `--check`: migration çalıştırmaz; güncelse `at head <revision>` ve 0, değilse `behind: <current> -> <head>` ve 3.

3. **`python -m ais0c_worker preflight`** (`preflight.py`). Her denetim bir satır yazar: `PASS|FAIL|WARN <ad> <ayrıntı>`. Bir `FAIL` varsa çıkış 1, yoksa 0 (`WARN` çıkışı değiştirmez). Sırayla:

   | Ad | Geçer | Kalır |
   |---|---|---|
   | `database` | revizyon head'de | head'de değil (`behind …`) ya da bağlanamıyor |
   | `shadow` | `writes_enabled` kapalı (T-23: shadow hiçbir şey yazmaz) | açık |
   | `skills_mode` | `AIS0C_SKILLS_MODE` boş ya da `prod` | `dev` (T-58: prod'da taslak aday olmaz) |
   | `skills` | onaylı skill sayısı ≥ 1 | 0 ise `WARN` (shadow skill'siz başlar) |
   | `model_registry` | `AIS0C_MODEL_REGISTRY` `config/models/registry.prod.yaml` ve `model_release.verify` 0 | başka dosya ya da `verify` ≠ 0 (H-7) |
   | `gateway` | `AIS0C_GATEWAY_URL/healthz` 200 ve `AIS0C_WORKER_SECRETS_DIR`'deki her `gateway-token-<profil>` ile o profilin araç listesi boş değil | gateway kapalı, token yok ya da liste boş |
   | `temporal` | `TEMPORAL_ADDRESS`'e bağlanır ve namespace'i görür | bağlanamaz |
   | `models` | registry'deki her alias LiteLLM'den (`LITELLM_BASE_URL`) `max_tokens: 1` ile cevap verir | bir alias cevap vermez |

   `--skip-models`: `models` denetimini atlar (`WARN models skipped`). `--json`: aynı sonuçları JSON listesi olarak yazar.

   Örnek çıktı:

   ```text
   PASS database      at head 0012_log_source_telemetry
   PASS shadow        writes_enabled is off
   PASS skills_mode   prod
   WARN skills        no approved skill; shadow runs without skills
   FAIL model_registry verify: soc-fast artifact_hash is empty (H-7)
   PASS gateway       5 profiles, every tool list non-empty
   PASS temporal      default
   WARN models        skipped
   preflight: 1 failed
   ```

4. Belgeler: iki komutun kısa bölümü (`services/worker/README.md`, `deploy/compose/README.md`'nin prod bölümü yoksa worker README'si yeter).

## Kabul kriterleri ve testler

1. **migrate** (`packages/activities/tests/test_deploy.py`, Postgres fixture'larıyla): `test_migrate_brings_an_empty_database_to_head`, `test_migrate_is_idempotent`, `test_database_revision_reports_behind`.
2. **migrate CLI** (`services/worker/tests/`): `test_migrate_check_returns_3_when_behind`, `test_migrate_without_database_url_exits_2`.
3. **shadow denetimi:** `test_preflight_fails_when_writes_are_enabled` (bayrak açık → `FAIL shadow`, çıkış 1); `test_preflight_passes_shadow_when_the_flag_row_is_missing`.
4. **skill modu:** `test_preflight_fails_in_dev_skills_mode`; `test_preflight_warns_without_approved_skills` (çıkış 0).
5. **model kaydı:** `test_preflight_fails_when_model_release_verify_fails` (`verify` 1 döndüren sahte); `test_preflight_fails_on_a_non_prod_registry` (`registry.dev.yaml`).
6. **gateway ve modeller** (sahte HTTP sunucusuyla, gerçek gateway ya da model yok): `test_preflight_fails_when_a_profile_has_no_tools`, `test_preflight_fails_when_an_alias_does_not_answer`, `test_skip_models_warns`.
7. **çıktı:** `test_preflight_json_lists_every_check` (8 denetimin hepsi, sırayla).

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest packages/activities services/worker -q
uv run pytest -q          # tam suite, ~7 dk; Temporal test sunucusu takılırsa PID ile durdur ve yeniden koş
uv run lint-imports
git grep -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu|space-bunny|opencode' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

## PR

`../ais0c-prs/PR-T-078.md`. Dev stack'e karşı örnek bir `preflight --skip-models` çıktısını koy (dev'de `skills_mode` ve `model_registry` beklenen biçimde `FAIL` verir; bu doğrudur).

## Kapsam dışı

- Compose servisleri (T-077), imajlar (T-076), canary'nin yazma kuralları.
- Gerçek modeli çağırmak: testlerde sahte; PR'daki örnek `--skip-models` ile.

## Notlar

- Güvenliğe dokunan bir denetim (kill switch) var: negatif testleri adlarıyla yaz. Kullanıcıya bu görev için yüksek effort önerilir.
- Lab'a ve gerçek modele bağlanılmaz; `deploy/compose/.env` okunmaz (PR örneği hariç; çıktıda sır olmamalı).
- Sözleşme değişikliği, izinli dosya dışı ihtiyaç ya da dokümanla çelişki çıkarsa dur ve PR'da yaz.
