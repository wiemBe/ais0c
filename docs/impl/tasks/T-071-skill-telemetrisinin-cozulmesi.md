# T-071: Telemetri sınıfları (4/4): skill bölümünde sınıfın kurulumdaki log source'larla çözülmesi

## Amaç

T-070'ten sonra Investigation'ın skill bölümü telemetriyi sınıf adıyla gösteriyor: `- waf (required):`. Bu görev her sınıfı bu kurulumun Analiz Kataloğu'na göre çözer. Prompt, o sınıfı karşılayan etkin log source'ların tip adlarını ve kimliklerini gösterir. Böylece ajan sorgusunu `logsourceid` ile daraltabilir. Sınıfın etkin log source'u yoksa prompt bunu açıkça söyler ve ajan data gap yazar (T-95 (5)).

Çözüm bir **activity**'de yapılır, çünkü Investigation'ın görevi Temporal workflow kodunda kurulur ve orada veritabanı okunamaz (AGENTS.md hard rule 4).

## Okunacaklar

Yalnızca bunlar:

- `docs/decisions.md`'de T-95 satırı, (5) maddesi
- **Akış:**
  - `packages/workflows/src/ais0c_workflows/evaluation.py:155-175` (`InvestigationInput`'un kurulduğu yer) ve `:536-548` (`_skill_of`);
  - `packages/workflows/src/ais0c_workflows/agent_runtime.py:108-145` (`_Input`, `InvestigationInput`);
  - `packages/activities/src/ais0c_activities/agent_runtimes.py:123-140` (`InvestigationInputs` Protocol), `:213-243` (`investigation_task`), `:349-355` (`_skill_input`);
  - `packages/activities/src/ais0c_activities/skills.py:81-100` (`skill_input`);
  - `packages/agents/src/ais0c_agents/skills.py` (`SkillTelemetry`, `render_skill`).
- **Activity deseni:** `packages/activities/src/ais0c_activities/chain.py:127-152` (`activities()` listesi, `candidate_skills` activity'si). İsim sabitleri iki yerde durur: `packages/activities/src/ais0c_activities/names.py` ve `packages/workflows/src/ais0c_workflows/names.py`. Workflow tarafındaki kayıt listesi `names.py:135-165`'tir.
- **Storage:** T-068'in `list_catalog_log_sources(session, telemetry_class=...)` filtresi (yalnızca etkin ve kalkmamış log source'ları döndürür)

## Branch ve worktree

```bash
git worktree add ../ais0c-T-071 -b agent/<araç>/T-071 main
cd ../ais0c-T-071
```

Ana checkout'ta çalışılmaz, orada branch değiştirilmez. Push yapılmaz.

## İzinli dizinler

- `packages/workflows/` (model, `evaluation.py`, `names.py`, testler)
- `packages/activities/` (yeni activity, isim, Protocol, `skill_input`, testler)
- `packages/agents/src/ais0c_agents/skills.py` ve `packages/agents/tests/`

`packages/contracts`, `packages/storage`, prompt şablonları ve skill'ler değişmez.

## Adımlar

Sırayla:

1. **Agents modeli** (`agents/skills.py`):

   ```python
   class SkillTelemetrySource(BaseModel):
       """The installation's enabled log sources of one type that serve a telemetry class."""
       model_config = ConfigDict(extra="forbid", frozen=True)
       telemetry_class: Slug
       type_name: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9 ._()/-]{1,255}$")] | None
       log_source_ids: Annotated[tuple[int, ...], Field(max_length=20)]
       total: Annotated[int, Field(ge=1)]
   ```

   - `type_name` güvenli karakter kalıbına uymuyorsa activity onu `None` yapar ve prompt'a tip adı girmez. Admin'in yazdığı özel tip adları da kalıba uymazsa girmez.
   - `SkillInput`'a `telemetry_sources: tuple[SkillTelemetrySource, ...] | None = None` eklenir. `None` "çözülmedi" demektir; boş tuple "hiçbir sınıfın log source'u yok" demektir.
   - `__init__.py`'den dışa aktarılır.
2. **Render** (`render_skill`). `telemetry_sources is None` ise çıktı bugünkü gibidir. Değilse her gereksinimin satırından sonra, event'lerden önce bir satır gelir:

   ```text
   - waf (required):
     - In this installation: F5 Networks BIG-IP ASM, 1 log source (logsourceid 21).
     - Request log with attack_type SQL-Injection: ...
   - firewall (optional):
     - In this installation: no enabled log source of this class; report a data gap for it.
     - Traffic from the web server to external addresses after the requests ...
   ```

   - Birden çok tip varsa tip başına bir satır yazılır, tip adına göre sıralı.
   - 20'den fazla log source varsa: `Microsoft Windows Security Event Log, 34 log sources (logsourceid 12, 15, ..., first 20 of 34).`
   - `type_name` `None` ise `a custom type` yazılır.
3. **Activity** (`activities/chain.py`, isim `SKILL_TELEMETRY = "skill_telemetry"`; iki `names.py`'ye ve iki kayıt listesine eklenir):

   ```python
   @activity.defn(name=SKILL_TELEMETRY)
   async def skill_telemetry(self, skill_id: str, version: str) -> list[SkillTelemetrySource]:
       """The installation's log sources for each telemetry class the skill asks for; [] when
       the skill is not loaded."""
   ```

   - Skill'in `required_telemetry`'sindeki her farklı sınıf için `list_catalog_log_sources(session, telemetry_class=cls)` çağrılır.
   - Sonuç tip adına göre gruplanır: kimlikler sıralanır, ilk 20'si alınır, `total` toplam sayıdır.
   - Yalnızca okur.
4. **Workflow modeli** (`agent_runtime.py`). Aynı JSON biçiminde bir model tanımlanır, çünkü workflows paketi agents'ı import edemez:

   ```python
   class TelemetrySource(_Input):
       telemetry_class: str
       type_name: str | None
       log_source_ids: tuple[int, ...]
       total: int
   ```

   `InvestigationInput`'a `telemetry: tuple[TelemetrySource, ...] | None = None` eklenir.
5. **Evaluation** (`evaluation.py`). Investigation adımında `_skill_of(step, candidates)` bir skill döndürüyorsa önce şu çağrılır:

   ```python
   telemetry = await call(SKILL_TELEMETRY, skill.skill_id, skill.version, result_type=list[TelemetrySource])
   ```

   Sonuç `InvestigationInput(telemetry=tuple(telemetry))`'e girer. Skill yoksa activity çağrılmaz, `telemetry=None` kalır.
6. **Activities tarafı.**
   - `InvestigationInputs` Protocol'üne `telemetry` özelliği eklenir.
   - `investigation_task` → `_skill_input(skills, skill, inputs.telemetry)` → `skill_input(skill, telemetry_sources=...)`. Workflow'dan gelen `TelemetrySource`'lar `SkillTelemetrySource`'a çevrilir.
   - `begin_agent_run` skill'i reddetmişse (`skill` `None`) telemetri kullanılmaz.

## Kabul kriterleri ve testler

1. **Render** (`packages/agents/tests/test_skill_section.py`):
   - `test_render_names_the_installation_sources`: yukarıdaki örnek metin aynen çıkar.
   - `test_render_says_when_a_class_has_no_source`: "no enabled log source of this class; report a data gap for it" satırı çıkar.
   - `test_render_without_resolution_is_unchanged`: `telemetry_sources=None` ile çıktı T-070'teki gibidir.
   - `test_render_caps_ids_at_20`: 34 log source'lu örnek.
   - Negatif test `test_unsafe_type_name_is_not_rendered`: `type_name="Ignore previous instructions"` kalıba uyar ama talimat kalıbı taşır. Bu yüzden ad, `SkillTelemetrySource`'a girmeden önce activity'de knowledge'ın skill tarayıcısıyla da kontrol edilir (`ais0c_knowledge.skills.scan_text(type_name)` bir bulgu döndürürse) ve `None` yapılır. Activity testi bunu gösterir.
2. **Activity** (`packages/activities/tests/test_skill_telemetry.py`, Postgres fixture'larıyla):
   - `test_sources_by_class_and_type`: T-068'in üç satırlık örneği ve bir `waf` satırı.
   - `test_disabled_and_missing_are_left_out`.
   - `test_unknown_skill_gives_nothing`: `[]` döner.
   - `test_unsafe_type_name_becomes_none`.
3. **Workflow** (`packages/workflows/tests/`, Temporal test ortamı ve sahte activity'lerle):
   - `test_investigation_input_carries_the_skill_telemetry`: skill'li Investigation adımında activity bir kez çağrılır, sonucu `InvestigationInput.telemetry`'ye girer.
   - `test_no_skill_no_telemetry_call`: skill'siz adımda activity çağrılmaz, `telemetry` `None`'dır.
   - İsim sabitleri testi (activities ve workflows'taki isimler aynıdır) yeni ismi de kapsar.
4. **Uçtan uca girdi** (`packages/activities/tests/`): `investigation_task` ile kurulan görevin skill bölümü, çözülmüş kaynakları içerir.

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest packages/agents packages/activities packages/workflows -q
uv run lint-imports
git grep -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

## PR

`../ais0c-prs/PR-T-071.md`. İçine bir Investigation skill bölümünün örnek çıktısı konur: `windows-kerberoasting`, sentetik katalog.

## Kapsam dışı

- Router'ın telemetri sınıfıyla tetiklemesi
- Verification'ın skill'i (Verification skill almaz)
- Prompt şablonu değişikliği: değişen yalnızca `{{ skill }}` bölümünün içeriğidir.

## Bağımlılıklar

- T-068 (storage filtresi) ve T-070 (`telemetry_class`) birleşmiş olmalı.

## Notlar

- **Workflow değişikliği:** `evaluation.py` yeni bir activity çağırıyor. Kod tabanı `workflow.patched` kullanmıyor; prod henüz açık değil. Birleştirmeden sonra planner dev Temporal'daki açık CaseWorkflow'ları sonlandırır (planner.md §8). Bu görevde patch eklenmez.
- Kullanıcıya bu görev için yüksek effort önerilir: workflow kodu ve determinizm söz konusu (planner.md §4, kural 11).
- Birleştirmeden sonra planner `skill-windows-dcsync` suite'ini koşar (k = 5) ve s4/s6 lab e2e'sinde prompt'un skill bölümünü kontrol eder.
