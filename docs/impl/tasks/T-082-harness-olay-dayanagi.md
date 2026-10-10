# T-082: Harness'te bulunan olayın dayanağı ve Verification kararının puanlanması

## Amaç

Codex'in T-080 incelemesi (2026-10-10) harness'in ölçümünde iki açık buldu. İkisi de T-052'den beri var:

1. **Bulunan olay dayanaksız sayılabiliyor.** `event_found` (`harness/src/ais0c_harness/eval/investigation.py:403-420`) bir aciliyet adayının adresi ve kullanıcıyı **adlandırmasını** yeterli sayar; adayın gösterdiği kanıtın satırlarında bu olayın bulunup bulunmadığına bakmaz. Kaynağı IP olan WAF offense'lerinde saldırganın adresi offense'in kendisinde yazar: model hiç sorgu sonucu okumadan onu adlandırıp `events_found`'u geçebilir.
2. **Scripted model IP offense'inde boş sorgu koşar.** `search_query` (`harness/src/ais0c_harness/eval/scripted_replay.py:44-54`) her offense için `username = '<offense_source>'` sorgular. `lab-45`, `lab-46`, `lab-48`, `lab-49`, `lab-52`'de kaynak bir IP adresidir, sorgu 0 satır döner. Senaryolar yine geçer, çünkü 1. açık bunu örter. Scripted testler bu yüzden yanlış bir güven veriyor.

Ayrıca: **Verification'ın kararı hiç puanlanmıyor.** `verification_checks` (`harness/src/ais0c_harness/eval/verification.py:254-275`) yalnızca `agrees` ve itiraz edilen claim'lere bakar; `VerificationResult.verdict` (`packages/contracts/src/ais0c_contracts/agents.py:117`) denetlenmez. T-066 (Verification v3) bunu isteyecek; bu görev isteğe bağlı bir beklenti ekler, mevcut senaryolar değişmez.

**Gerçek model çağrılmaz.** Bütün testler scripted ya da elle kurulmuş sonuçlarla koşar.

## Okunacaklar

- `harness/src/ais0c_harness/eval/investigation.py:1-25` (modül docstring'i, `events_found`'un tanımı), `:96-110` (`ExpectedEvent`), `:335-420` (`investigation_checks`, `cited_claim_evidence`, `event_found`)
- `harness/src/ais0c_harness/eval/verification.py:60-90` (`VerificationExpectation`), `:230-280` (`verification_checks`)
- `harness/src/ais0c_harness/eval/scripted_replay.py` (bütün dosya, ~200 satır)
- `harness/tests/test_eval_replay_suites.py:230-350` (mevcut negatif testler ve `test_an_event_is_found_by_a_candidate_or_by_a_cited_row`)
- `harness/suites/investigation-gold/README.md` ve `verification-gold/README.md` "Format" bölümleri
- `packages/contracts/src/ais0c_contracts/agents.py` (`UrgentEvent.evidence_id`, `VerificationResult`); sözleşme değişmez.

## Branch ve worktree

Worktree planner tarafından açılır: `../ais0c-T-082`, `main`'den (T-060 ve T-080 birleştikten sonra). Yalnızca orada çalışılır. Ana checkout'ta çalışılmaz, branch değiştirilmez, push yapılmaz (AGENTS.md hard rule 9).

## İzinli dosyalar

- `harness/src/ais0c_harness/eval/investigation.py`, `verification.py`, `scripted_replay.py`
- `harness/tests/test_eval_replay_suites.py`; yeni `harness/tests/test_eval_grounding.py`
- `harness/suites/investigation-gold/README.md`, `harness/suites/verification-gold/README.md`: yalnızca "Format" tabloları
- `harness/README.md`: yalnızca `events_found`'u anlatan cümle (varsa)

Senaryo dosyaları, kayıtlar, `packages/` ve prompt'lar değişmez. Bir senaryo yeni kuralla scripted modelde düşüyorsa senaryoyu değiştirme: dur ve PR'da yaz.

## Adımlar

1. **`event_found`'un yeni kuralı.** İmza:

   ```python
   def event_found(
       expected: ExpectedEvent,
       candidates: Sequence[UrgentEvent],
       rows_by_evidence: Mapping[str, Sequence[Mapping[str, JsonValue]]],
       cited: Collection[str],
   ) -> bool:
       """Whether the run shows the expected event in rows it retrieved.

       A candidate counts when it names the address (as source or destination) and the user, and
       the rows of the evidence it cites hold both. A claim or a timeline entry counts when a row
       of evidence it cites holds both.
       """
   ```

   `rows_by_evidence`: bu koşunun yürütülmüş (`exchange.executed`) araç sonuçlarının `evidence_id → data` eşlemi (`investigation_checks`'te kurulur). `cited`: claim ve timeline'ın gösterdiği kanıtlar (`cited_claim_evidence`). Bir satırın olayı "tutması": satırın değerlerinden biri adrese eşit ve (kullanıcı beklenmişse) biri kullanıcıya eşit (bugünkü `values` denetimi). Görevin verdiği kanıt (`context_evidence`) araç sonucu değildir, satır kaynağı sayılmaz.
2. **Modül docstring'i ve README'ler** yeni kuralı söyler (`events_found` maddesi; Format tablosundaki `expect.find_events` satırı).
3. **Scripted sorgu.** `search_query` offense türüne bakar: `offense.offense_type == "Source IP"` ise `sourceip = '<offense_source>'`, diğer bütün türlerde bugünkü gibi `username = '<offense_source>'`. Tırnak kaçışı aynı kalır. Scripted cevabın adayları (`investigation_answer`) ve timeline'ı yine `RESULTS_ALIAS`'ı (`ev_3`) gösterir; adayların serbest metinleri DCSync'e özgü olmaktan çıkar: `log_source` `"Recorded events"`, `event_name` `"The expected event"`, `reason` `"The event the scenario expects, in the search results."`, `hypotheses[0].text` boşluk yoksa `"The offense's activity is malicious."`.
4. **Verification `verdict_in`.** `VerificationExpectation`'a isteğe bağlı alan:

   ```python
   verdict_in: frozenset[CaseVerdict] = frozenset()
   """Verdicts the verifier may return; empty: the verdict is not scored."""
   ```

   Boş değilse `verification_checks` `VERDICT_IN` adlı bir denetim ekler (`investigation.py`'deki gibi, aynı ayrıntı metni). Scripted `verification_answer`'ın `verdict`'i: `verdict_in` boşsa ya da gözden geçirilen karar içindeyse gözden geçirilen karar, değilse `verdict_in`'in sıralı ilki. Mevcut senaryoların hiçbiri alanı kullanmaz.

## Kabul kriterleri ve testler

1. **Dayanaksız aday sayılmaz.** `test_a_candidate_whose_evidence_lacks_the_event_is_not_found`: aday `192.0.2.15`/`svc_backup`'ı adlandırır, gösterdiği kanıtın satırlarında bu adres yok → `event_found` `False`.
2. **Dayanaklı aday sayılır.** `test_a_candidate_backed_by_its_rows_is_found`: aynı aday, satırlarında adres ve kullanıcı olan kanıtı gösterir → `True`. `test_a_candidate_citing_handed_evidence_is_not_found`: aday görevin verdiği kanıtı (`ev_c1`) gösterir → `False`.
3. **Claim yolu değişmez.** `test_a_cited_row_still_finds_the_event`: aday yok, claim'in gösterdiği kanıtın satırı olayı tutar → `True`. Mevcut `test_an_event_is_found_by_a_candidate_or_by_a_cited_row` yeni kurala göre güncellenir (adı da: `test_an_event_is_found_by_a_backed_candidate_or_a_cited_row`).
4. **Scripted sorgu türe uyar.** `test_the_scripted_query_follows_the_offense_type`: `lab-45-waf-sqli` için sorgu `sourceip = '198.51.100.23'` içerir, `lab-30-dcsync` için `username = 'svc_backup'`. `test_the_scripted_search_returns_rows_for_ip_offenses`: `lab-45`, `lab-46`, `lab-48`, `lab-49`, `lab-52`'nin her biri için scripted sorgunun replay'deki sonucu en az bir satırdır.
5. **Bütün suite'ler yeni kuralla geçer.** Mevcut scripted testler (`test_every_gold_scenario_passes_with_a_scripted_model_k_2`, `test_skill_windows_dcsync.py`, `test_skill_web_sql_injection.py`, `test_skill_password_spraying.py`) değişmeden geçer. Biri düşerse dur ve PR'da yaz.
6. **Verification kararı.** `test_verification_verdict_is_scored_when_expected`: `verdict_in: [fp]` olan (geçici dizinde kurulmuş) bir senaryoda `verdict: tp` cevabı yalnızca `verdict_in` denetimini düşürür. `test_verification_verdict_is_not_scored_by_default`: alan yokken denetim listesinde `verdict_in` yoktur.

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest harness -q
uv run lint-imports
uv run pytest -q                  # tam suite, ~8 dk; PR'dan önce zorunlu
git grep -I -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu|space-bunny|opencode' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

## PR

`../ais0c-prs/PR-T-082.md`, `.github/pull_request_template.md` biçiminde.

## Durma noktaları

- Bir senaryo yeni `event_found` kuralıyla scripted modelde düşüyor: senaryo ya da kayıt değişmez; dur ve PR'da yaz.
- Sözleşme değişikliği gerekiyor: dur ve PR'da yaz.

## Notlar

- Gerçek model komutu koşma.
- Bu görev ölçümün kuralını sıkılaştırır: gerçek model koşularında `events_found` oranı düşebilir. Bu beklenen; önceki raporlarla karşılaştırılırken PR'da bir cümleyle belirtilir.
- Effort: orta; ölçüm bütünlüğü önemli, testler eksiksiz olmalı.
