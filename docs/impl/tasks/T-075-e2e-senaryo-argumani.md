# T-075: Lab e2e'si log üretecine seçilen senaryoyu vermiyor

## Amaç

`tests/e2e/test_lab_triage.py:381-401` (`_send_scenario`) log üretecini `--scenario` bayrağıyla ama **değeri olmadan** çağırıyor:

```text
python -m ais0c_harness.loggen run --scenario --target 192.0.2.10:514 --seed 12 ...
```

T-058 (`289e40da`) eski `SCENARIO` sabitini kaldırdı, yerine `settings.scenario`'yu koymadı. Bu yüzden 2026-10-07'den beri lab e2e'si başlar başlamaz `CalledProcessError` ile düşüyor (planner'ın 2026-10-09 s6 koşusu, 10 saniyede). `test_scenario_selection.py` senaryo seçimini test ediyor ama üretecin komutunu test etmiyor; hata bu yüzden görünmedi.

## Okunacaklar

- `tests/e2e/test_lab_triage.py:381-401`
- `tests/e2e/e2e_support.py:150-210` (`LabSettings`, `scenario`, `seed`, `syslog_target`)
- `tests/e2e/test_scenario_selection.py` (bütün dosya; lab gerektirmeyen testlerin deseni)
- `harness/src/ais0c_harness/loggen/__main__.py:160-175` (`--scenario`: uzantısız senaryo adı, örnek `s6-waf-sqli-gecti`)

## Branch ve worktree

```bash
git worktree add ../ais0c-T-075 -b agent/<araç>/T-075 main
cd ../ais0c-T-075
```

Ana checkout'ta çalışılmaz, branch değiştirilmez, push yapılmaz.

## İzinli dosyalar

- `tests/e2e/test_lab_triage.py`, `tests/e2e/e2e_support.py`, `tests/e2e/test_scenario_selection.py`

## Adımlar

1. `e2e_support.py`'ye komutu kuran saf bir fonksiyon:

   ```python
   def loggen_command(settings: LabSettings, out_dir: Path, *, python: str = sys.executable) -> list[str]:
       """The generator's command line for the selected scenario (T-008, T-058)."""
   ```

   Dönen liste: `[python, "-m", "ais0c_harness.loggen", "run", "--scenario", settings.scenario, "--target", settings.syslog_target, "--seed", settings.seed, "--speed", "100000", "--out-dir", str(out_dir)]`.
2. `_send_scenario` bu fonksiyonu kullanır; `subprocess.run`'ın diğer argümanları (`check=True`, `cwd=REPO_ROOT`) aynı kalır.
3. Testler (`test_scenario_selection.py`, lab ve ağ gerektirmez, atlanmaz).

## Kabul kriterleri ve testler

1. `test_the_generator_command_names_the_selected_scenario`: `AIS0C_E2E_SCENARIO=s6-waf-sqli-gecti` ile kurulan `LabSettings` için komutta `--scenario`'dan hemen sonra `s6-waf-sqli-gecti` gelir.
2. `test_the_generator_command_uses_the_default_scenario`: senaryo verilmezse `--scenario`'dan sonra `s2-dcsync` gelir.
3. `test_every_option_of_the_generator_command_has_a_value`: komuttaki her `--` ile başlayan öğeden sonra `--` ile başlamayan, boş olmayan bir değer gelir (bu hatanın genel hali).
4. `test_the_generator_accepts_the_command`: komut `--dry-run` eklenerek ve `--target` çıkarılarak gerçekten çalıştırılır (`subprocess.run(..., check=True)`, `cwd=REPO_ROOT`), syslog'a bir şey gönderilmez. `--dry-run`'ın davranışı `loggen/__main__.py`'den doğrulanır; desteklemiyorsa bu kriter atlanmaz, PR'da yazılır.

`LabSettings`'i test için kurarken `test_scenario_selection.py`'deki mevcut yardımcı kullanılır (sahte host ve token; gerçek değer yok).

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest tests/e2e -q        # lab testleri atlanır; seçim testleri koşar
uv run pytest -q                  # tam suite, ~7 dk
uv run lint-imports
git grep -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu|space-bunny|opencode' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

## PR

`../ais0c-prs/PR-T-075.md`.

## Notlar

- Lab'a bağlanma, syslog gönderme, gerçek model çağırma. Lab koşusunu planner yapar.
- Gerçek adres ya da token yazılmaz; testlerde RFC 5737 adresi (`192.0.2.10`).
