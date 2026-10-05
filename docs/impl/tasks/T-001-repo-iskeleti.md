# T-001: Repo iskeleti ve CI

## Amaç

Boş ama çalışan bir Python monorepo kurmak. Bu görev bittiğinde her paket import edilebilir olur, `AGENTS.md`'deki kontrol komutları çalışır ve her PR'da CI koşar. Sonraki bütün görevler bu iskeletin üzerine kurulur.

## Okunacaklar

- `AGENTS.md` (tamamı)
- `docs/impl/repo-structure.md` (tamamı)
- `docs/impl/multi-agent-dev.md` → "CI kontrolleri"

## İzinli dizinler

- Kök: `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `.gitleaks.toml`
- `packages/{contracts,policy,querylang,storage,knowledge,agents,activities,workflows,executor}/`: yalnızca iskelet
- `services/api/`, `services/worker/`: yalnızca iskelet
- `harness/`: yalnızca iskelet
- `.github/workflows/`

Bu dizinlerin dışında hiçbir dosya değiştirilmez.

## Kullanılan sözleşmeler

Yok.

## Kabul kriterleri

Bu görev altyapı kurduğu için bazı kriterler test yerine PR'da gösterilen komut çıktısıyla kanıtlanır; bunlar "(PR çıktısı)" ile işaretlidir.

1. Temiz bir klonda `uv sync` hatasız çalışır. (PR çıktısı)
2. Her paket, `services/api`, `services/worker` ve `harness` src düzeninde iskelete sahiptir (`<dizin>/src/ais0c_<ad>/__init__.py`). Her birinin bir import testi vardır.
3. Kökten şu komutlar çalışır ve geçer: `uv run ruff check`, `uv run ruff format --check`, `uv run pyright`, `uv run pytest`, `uv run lint-imports`.
4. import-linter konfigürasyonu, `repo-structure.md`'deki bağımlılık tablosunu birebir uygular. Geçici bir ihlal eklendiğinde (örnek: `ais0c_contracts` içinden `ais0c_storage` importu) `lint-imports` başarısız olur. (PR çıktısı)
5. pyright, `packages/contracts` ve `packages/policy` için strict moddadır.
6. pytest'te `lab` marker'ı tanımlıdır. `QRADAR_LAB_URL` ortam değişkeni yoksa `lab` işaretli testler atlanır. Bunu gösteren bir test vardır.
7. GitHub Actions iş akışı her PR'da şu adımları koşar: ruff, pyright, pytest, import-linter, gitleaks ve sağlayıcı adı kontrolü.
8. Sağlayıcı adı kontrolü: `packages/`, `services/`, `apps/`, `prompts/` ve `harness/` altında `openai`, `openrouter`, `deepseek`, `anthropic`, `qwen`, `glm`, `zhipu` kelimeleri (büyük/küçük harf duyarsız) geçerse CI başarısız olur. Tek istisna `packages/agents/src/ais0c_agents/llm.py`'dir. Geçici bir ihlalle başarısız olduğu gösterilir. (PR çıktısı)
9. `.gitignore`; `.env`, `.venv/`, `__pycache__/`, `.pytest_cache/` ve `*.pyc` dosyalarını dışlar.

## Kapsam dışı

- Her türlü iş mantığı ve Pydantic modeli (T-002)
- Arayüz iskeleti (`apps/ui`, Faz 1)
- Docker Compose (T-003)
- `services/mcp-gateway` (T-011)

## Bağımlılıklar

Yok. İlk görevdir.

## Notlar

- Python 3.12. uv workspace üyeleri: bütün `packages/*`, `services/api`, `services/worker`, `harness`.
- ruff kuralları için makul ve katı bir başlangıç seç (örnek: `E`, `F`, `I`, `B`, `UP`, `S`, `RUF`). Seçimi PR'da kısaca gerekçelendir.
- Bağımlılık eklerken sürüm aralığı ver; kilitlemeyi `uv.lock` yapar.
