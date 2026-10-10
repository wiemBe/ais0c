# T-084: Verification görevine kurum olguları (`org_context`)

## Amaç

Investigation görevine katalog olguları `org_context` olarak girer (`packages/agents/src/ais0c_agents/investigation.py:360-362`, `render_org_context`). Verification görevine girmez: `VerificationInput` (`packages/workflows/src/ais0c_workflows/agent_runtime.py:153-164`) zenginleştirmeyi taşımaz, `verification_task` (`packages/activities/src/ais0c_activities/agent_runtimes.py:276-296`) onu `VerificationTask`'a koymaz.

Sonuç: Verification onaylı bir tarayıcının `fp` kararını (kural 100359'un katalog notu, s9) bilemez. T-066'nın Verification v3'ü T-88'i uygulayacak ("yetki yalnızca `org_context` olgusundan"); bunun için olguları görmesi gerekir (T-066 dosyasının 2026-10-10 eki, karar T-116).

Bu görev yalnızca **veriyi taşır**: workflow girdisi, activity, ajan görevi ve render. Prompt değişmez: `prompts/verification/v2.md`'de `{{ org_context }}` yer tutucusu yok ve `PromptTemplate.render` kullanılmayan bir değeri reddeder (`packages/agents/src/ais0c_agents/prompts.py:75-89`). Bu yüzden render `org_context`'i **yalnızca şablonda yer tutucu varsa** verir. Yer tutucuyu T-066'nın v3'ü ekler.

## Okunacaklar

- `packages/workflows/src/ais0c_workflows/agent_runtime.py:137-170` (`InvestigationInput`, `VerificationInput`)
- `packages/workflows/src/ais0c_workflows/evaluation.py:180-205` (Verification adımının girdisi; `enrichment` aynı fonksiyonda var)
- `packages/activities/src/ais0c_activities/agent_runtimes.py:130-175` (`VerificationInputs` protokolü), `:276-296` (`verification_task`)
- `packages/agents/src/ais0c_agents/verification.py:137-148` (`VerificationTask`), `:248-281` (`render_instructions`)
- `packages/agents/src/ais0c_agents/investigation.py:345-365` (desen: `render_org_context(enrichment.catalog, critical_assets=enrichment.critical_asset_hits)`)
- `packages/agents/src/ais0c_agents/prompts.py:55-89` (`PromptTemplate.placeholders`, `render`)
- `harness/src/ais0c_harness/eval/verification.py:105-160` (`_Inputs`, adaptörün görevi `verification_task` ile kurması)
- `docs/impl/prompts.md` "Kurum olguları (`org_context`)" bölümü

## Branch ve worktree

Worktree planner tarafından açılır: `../ais0c-T-084`, `main`'den. Yalnızca orada çalışılır. Ana checkout'ta çalışılmaz, branch değiştirilmez, push yapılmaz (AGENTS.md hard rule 9).

## İzinli dosyalar

- `packages/workflows/src/ais0c_workflows/agent_runtime.py`, `packages/workflows/src/ais0c_workflows/evaluation.py` ve `packages/workflows/tests/`
- `packages/activities/src/ais0c_activities/agent_runtimes.py` ve `packages/activities/tests/`
- `packages/agents/src/ais0c_agents/verification.py` ve `packages/agents/tests/`
- `harness/src/ais0c_harness/eval/verification.py` ve `harness/tests/`

`packages/contracts`, prompt'lar, manifest'ler (`config/agents/`) ve senaryolar değişmez.

## Adımlar

1. **Workflow girdisi.** `VerificationInput`'a `enrichment: EnrichmentContext | None = None` (varsayılan `None`: açık workflow'ların kaydedilmiş girdileri okunabilir kalır). `evaluation.py`'deki Verification adımı `enrichment=enrichment` verir. Komut sırası değişmez (yalnızca activity girdisine bir alan).
2. **Activity.** `VerificationInputs` protokolüne `enrichment` (`EnrichmentContext | None`). `verification_task` `VerificationTask(..., enrichment=inputs.enrichment)`.
3. **Ajan görevi.** `VerificationTask`'a `enrichment: EnrichmentContext | None = None`.
4. **Render.** `render_instructions`'ta değerler sözlüğüne, **yalnızca** `"org_context" in self.prompt.placeholders` ise:

   ```python
   values["org_context"] = (
       render_org_context(task.enrichment.catalog, critical_assets=task.enrichment.critical_asset_hits)
       if task.enrichment is not None
       else render_org_context(CatalogContext(rules=[], log_sources=[]))
   )
   ```

   (`CatalogContext`'in iki alanı zorunludur: `rules`, `log_sources`; `packages/contracts/src/ais0c_contracts/offense.py:62-66`.) Yer tutucu yoksa sözlük bugünküyle aynıdır.
5. **Harness.** Verification adaptörünün `_Inputs`'u kaydın zenginleştirmesini (`recording.enrichment`) `enrichment` olarak verir.

## Kabul kriterleri ve testler

1. **Workflow taşır.** `packages/workflows/tests/`: `test_verification_input_carries_the_enrichment` (zincirin Verification adımına giden `VerificationInput`'ta `enrichment` vakanın zenginleştirmesidir; mevcut zincir testlerinin deseniyle, Temporal test ortamında) ve `test_a_verification_input_without_enrichment_still_loads` (alanı olmayan eski bir JSON girdisi `None` ile okunur).
2. **Activity taşır.** `packages/activities/tests/`: `test_verification_task_passes_the_enrichment`.
3. **v2 değişmez.** `packages/agents/tests/`: `test_verification_v2_prompt_is_unchanged_with_enrichment` (v2 şablonuyla, `enrichment` dolu ve boş iki görevin render'ı birbirinin ve bugünkü çıktının aynısıdır; `PromptError` yok).
4. **Yer tutucu varsa olgular girer.** `test_org_context_is_rendered_when_the_template_asks_for_it`: geçici bir şablon (v2'nin metni + `{{ org_context }}`) ile, katalog notu "Internal vulnerability scanner LabVulnScan (192.0.2.79) …" olan bir zenginleştirme → render'da `<org_context>` bloğu ve not var; `enrichment=None` → boş olgu bloğu, hata yok.
5. **Güvenlik (negatif).** `test_org_context_never_takes_untrusted_text`: zenginleştirmedeki bir varlık açıklaması (`critical_asset_hits`) ya da log source açıklaması `org_context`'e Investigation'daki kurallarla girer, fazlası değil: `render_org_context`'in Investigation testlerindeki aynı ayıklama (etiket benzeri ifadelerin etkisizleştirilmesi) burada da geçerlidir; `</org_context>` içeren bir not bloğu kapatamaz.
6. **Harness.** `harness/tests/`: Verification adaptörünün kurduğu görevde `enrichment` kaydınkidir (`test_the_verification_adapter_passes_the_recordings_enrichment`); mevcut gold testleri geçer.

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run lint-imports
uv run pytest -q                  # tam suite, ~8 dk; PR'dan önce zorunlu
git grep -I -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu|space-bunny|opencode' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

## PR

`../ais0c-prs/PR-T-084.md`, `.github/pull_request_template.md` biçiminde.

## Durma noktaları

- `packages/contracts`'ta bir değişiklik gerekiyor: dur ve PR'da yaz.
- Workflow'un komut sırası değişiyor (yeni activity, yeni zamanlayıcı): dur ve PR'da yaz; bu görev yalnızca bir girdi alanı ekler.

## Notlar

- Gerçek model ve lab yok.
- Workflow ve güvenlik sınırı (güvenilir katman) değişikliği: planner yüksek effort önerir.
- Birleşince planner dev Temporal'daki açık CaseWorkflow'ları sonlandırır.
