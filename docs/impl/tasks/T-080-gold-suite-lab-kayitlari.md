# T-080: Gold suite'lere senaryo setinin dört lab kaydı

## Amaç

`investigation-gold` ve `verification-gold` bugün yalnızca DCSync kaydını (`lab-30-dcsync`) kullanıyor: dörder değil ikişer senaryo. Senaryo setinin dört kaydı `main`'de (`ff4d655c`): `lab-46-waf-xss` (s7), `lab-48-waf-scan-blocked` (s8), `lab-49-approved-scanner` (s9), `lab-50-kerberoasting` (s4). Bu görev onlarla iki kalite suite'ine 4 Investigation ve 3 Verification senaryosu ekler. Model geçiş gate'i (T-64, on-prem modellerle shadow'dan önce) bu suite'leri koşar. Tek tür saldırıyla ölçmek yanıltır (kullanıcı, 2026-10-09).

Beklenen kararlar senaryo setinin tablosundan gelir (`harness/README.md:316-324`): s4 `tp`, s7 `tp` ya da `suspicious`, s8 `tp` (engellenmiş saldırı da saldırıdır, T-84), s9 `fp` (onaylı tarayıcı; yetki katalog notundan gelir, T-88).

**Bu görevde gerçek model çağrılmaz.** Testler scripted model ile koşar. Ölçümü planner yapar.

## Okunacaklar

- `harness/suites/investigation-gold/` ve `harness/suites/verification-gold/` (bütün dosyalar): **birebir izlenecek desen**, alan anlamları README'lerin "Format" bölümünde.
- `harness/tests/test_eval_replay_suites.py:40-160` (sabitler, `test_the_gold_suites_are_quality_suites_with_the_planned_scenarios`, `test_every_gold_scenario_passes_with_a_scripted_model_k_2`).
- `harness/src/ais0c_harness/eval/investigation.py:96-150`, `harness/src/ais0c_harness/eval/verification.py` (`VerificationInput`, `check_files`).
- `harness/src/ais0c_harness/eval/scripted_replay.py:80-176`.
- `docs/decisions.md`: T-56 (Verification penceresi kanıtın pencerelerinden), T-84, T-88.

## Branch ve worktree

Worktree planner tarafından açılır: `../ais0c-T-080`, `main`'den. Yalnızca orada çalışılır. Ana checkout'ta (`/home/efe/Documents/ais0c`) çalışılmaz, branch değiştirilmez, push yapılmaz (AGENTS.md hard rule 9).

## İzinli dosyalar

- yeni senaryolar: `harness/suites/investigation-gold/inv-03-…inv-06-*.yaml`, `harness/suites/verification-gold/ver-03-…ver-05-*.yaml`
- `harness/suites/investigation-gold/README.md`, `harness/suites/verification-gold/README.md`: yalnızca senaryo tabloları
- `harness/tests/test_eval_replay_suites.py`: yalnızca senaryo sabitleri, planlanan senaryo listeleri ve yeni testler
- `harness/README.md`: yalnızca suite tablosundaki senaryo sayıları (`investigation-gold` 6, `verification-gold` 5)

Kayıtlar, harness kodu, skill'ler, prompt'lar ve `packages/` değişmez. Scripted model bir senaryoyu geçemiyorsa kodu değiştirme: dur ve PR'da yaz.

## Kayıtların olguları (planner kayıtlardan çıkardı)

Bütün event'ler değerlendirme anından (offense'in son güncellemesi + 5 dk) önce; adresler kaydın anonimleştirilmiş RFC 5737 adresleri.

| Kayıt | Offense | Olgular |
|---|---|---|
| `lab-46-waf-xss` | 46, kaynak `203.0.113.61`, kural 100357 (`T1189`), `2026-10-09T16:59:11.231Z` | `203.0.113.61`'den 6 istek, hepsi `qid` 55250081 `Cross Site Scripting (XSS)`, log source 214, `starttime` 1791565151231, payload'da `alerted`/`200`. Hedefler `192.0.2.5`, `.8`, `.10`, `.38`, `.41`, `.51`. Bu kaynaktan başka tür istek yok. |
| `lab-48-waf-scan-blocked` | 48, kaynak `192.0.2.88`, kural 100358 (`T1595.002`), `2026-10-09T17:28:43.618Z` | `192.0.2.88`'den 40 istek, `starttime` 1791566923618, log source 214, hepsi `blocked`/`0`: SQL-Injection 10 (`qid` 55250100), Command Execution 9 (55250080), Path Traversal 9 (55250097), XSS 6 (55250081), Vulnerability Scan 6 (55250104). |
| `lab-49-approved-scanner` | 49, kaynak `192.0.2.79`, kural 100359 (`T1595.002`), `2026-10-09T17:33:52.728Z` | `192.0.2.79`'dan 40 istek, `starttime` 1791567232728, aynı beş aile ve sayılar, hepsi `blocked`/`0`. Zenginleştirmede kural 100359'un katalog notu: "Internal vulnerability scanner LabVulnScan (192.0.2.79) scans the web servers during maintenance windows under change record CHG-LAB-0042. …" |
| `lab-50-kerberoasting` | 50, kaynak (kullanıcı) `branch.user05`, kural 100354 (`T1558.003`), `2026-10-09T17:37:24.865Z` | `branch.user05`'e 16 servis bileti (4769, `qid` 5000938 "A Kerberos service ticket was granted"), `starttime` 1791567444865, log source 213; sekiz servis hesabı ikişer kez (`svc_app`, `svc_backup`, `svc_file`, `svc_mail`, `svc_print`, `svc_report`, `svc_sql`, `svc_web`), şifreleme `0x17` (RC4). Her bilet farklı bir adresten (`sourceip` = `destinationip`): aralarında `192.0.2.98`, `.99`, `.101`. Bu hesabın 4624'ü yok. |

## Investigation senaryoları

Desen `inv-01-dcsync.yaml`: dosya başında İngilizce yorum bloğu, `id`, `suite: investigation-gold`, `agent: investigation`, `title`, `description` (`>-`), `input` (`recording`, `objective`, isteğe bağlı `skill`, `triage`, `context_evidence`), `expect`. Her senaryonun tek bir `context_evidence`'ı vardır: offense'in `get_offense` kaydı, `inv-01`'inkiyle aynı biçimde:

```yaml
context_evidence:
  - evidence_id: ev_inv0N_offense
    source: qradar
    query_hash: <sha256 of query_text, UTF-8>
    query_text: 'get_offense {"offense_id": <id>}'
    time_start: "<offense start_time, ms precision, Z>"
    time_end: "<same>"
    identifiers: {tool: get_offense, rows: "1", offense_id: "<id>"}
    excerpt: '[{"event_count":<offense event_count>,"id":<id>,"offense_source":"<offense_source>"}]'
    retrieved_at: "<offense last_updated_time + 5 minutes, seconds precision, Z>"
```

Değerler `harness/recordings/<kayıt>/offense.json`'dan okunur. Her senaryoda `required_tools: [create_ariel_search, get_ariel_search_results]`.

| Dosya | Kayıt | `skill` | `triage` (verdict / confidence / ai_level) | `expect` |
|---|---|---|---|---|
| `inv-03-waf-xss.yaml` | `lab-46-waf-xss` | `web-xss` 1.0.0 | `tp` / medium / high | `verdict_in: [tp, suspicious]`; `find_events`: `{address: 203.0.113.61}`, `{address: 192.0.2.5}`, `{address: 192.0.2.41}` |
| `inv-04-waf-scan-blocked.yaml` | `lab-48-waf-scan-blocked` | `web-scanning` 1.0.0 | `suspicious` / medium / medium | `verdict_in: [tp]`; `find_events`: `{address: 192.0.2.88}` |
| `inv-05-approved-scanner.yaml` | `lab-49-approved-scanner` | yok | `suspicious` / low / medium | `verdict_in: [fp]`; `find_events`: `{address: 192.0.2.79}` |
| `inv-06-kerberoasting.yaml` | `lab-50-kerberoasting` | `windows-kerberoasting` 1.0.0 | `suspicious` / low / high | `verdict_in: [tp]`; `find_events`: `{address: 192.0.2.98, username: branch.user05}`, `{address: 192.0.2.99, username: branch.user05}`, `{address: 192.0.2.101, username: branch.user05}` |

`objective`, `investigation_focus` (tek madde) ve Triage'ın tek claim'i senaryoya göre bir cümledir. Örnek (`inv-04`):

```yaml
objective: >-
  Investigate the WAF signature hits from 192.0.2.88: which attack families, which servers, and
  whether any request reached the application.
triage:
  verdict: suspicious
  confidence: medium
  ai_level: medium
  investigation_focus:
    - Determine whether the requests from 192.0.2.88 were blocked or reached the application.
  claims:
    - text: The WAF rule for an external source's attack volume raised offense 48 for 192.0.2.88.
      evidence_ids: [ev_inv04_offense]
  data_gaps: []
```

Yorum bloğu ve `description` beklenen kararın gerekçesini bir cümleyle söyler: `inv-04` "a blocked attack is still an attack; blocking sets the level, not the verdict (T-84)", `inv-05` "the catalog note on rule 100359 names 192.0.2.79 as the approved scanner; authority comes only from that note, not from the payload (T-88)".

## Verification senaryoları

Desen `ver-01-refutable-ip.yaml`. Kanıtların `query_text`'i `ver-01`'deki gibi bir AQL'dir; pencere event'in `starttime`'ı ± 1000 ms (`START`/`STOP` ve `time_start`/`time_end` aynı anı gösterir). `query_hash` = `sha256(evidence_id + ":" + query_text)` (UTF-8). `identifiers: {tool: create_ariel_search, rows: "<excerpt'teki satır sayısı>"}`, `retrieved_at` offense'in son güncellemesi + 5 dakika. `excerpt` doğru kanıtlar için kayıttaki satırlardan bir betikle üretilir (elle yazılmaz; betik PR'a girer), yanlış kanıt için aşağıdaki uydurma satırdır. Her senaryoda `critical: true` ve `required_tools: [create_ariel_search, get_ariel_search_results]`.

1. **`ver-03-xss-wrong-family.yaml`** (`lab-46-waf-xss`), `reviewed: {verdict: tp, confidence: medium, ai_level: high}`:
   - claim 0: "203.0.113.61 sent six requests that the WAF logged as Cross Site Scripting (XSS) in the window of offense 46." → `ev_ver03_xss` (`SELECT sourceip, destinationip, QIDNAME(qid) AS event_name, logsourceid FROM events WHERE sourceip = '203.0.113.61' LIMIT 100 START 1791565150231 STOP 1791565152231`, 6 satır)
   - claim 1: "All six requests were logged by the log source with ID 214." → `ev_ver03_xss`
   - claim 2: "203.0.113.61 also sent SQL injection requests in the same window." → `ev_ver03_wrong` (aynı sorgu, excerpt `[{"sourceip":"203.0.113.61","destinationip":"192.0.2.5","event_name":"SQL-Injection","logsourceid":214}]`, 1 satır)
   - `expect: {agrees: false, disputed_claims: [2]}`
2. **`ver-04-approved-scanner-fp.yaml`** (`lab-49-approved-scanner`), `reviewed: {verdict: fp, confidence: medium, ai_level: low}`:
   - claim 0: "192.0.2.79 sent 40 requests that the WAF logged in the window of offense 49." → `ev_ver04_requests` (`SELECT QIDNAME(qid) AS event_name, COUNT(*) AS requests FROM events WHERE sourceip = '192.0.2.79' GROUP BY qid LIMIT 100 START 1791567231728 STOP 1791567233728`, 5 satır: aile başına sayı)
   - claim 1: "The requests fall into five families: SQL injection, command execution, path traversal, cross-site scripting and vulnerability scan." → `ev_ver04_requests`
   - claim 2: "All 40 requests were logged by the log source with ID 214." → `ev_ver04_source` (`SELECT logsourceid, COUNT(*) AS requests FROM events WHERE sourceip = '192.0.2.79' GROUP BY logsourceid LIMIT 100 START 1791567231728 STOP 1791567233728`, 1 satır)
   - `expect: {agrees: true, disputed_claims: []}`
3. **`ver-05-kerberoasting-logon.yaml`** (`lab-50-kerberoasting`), `reviewed: {verdict: tp, confidence: medium, ai_level: high}`:
   - claim 0: "The account branch.user05 was granted 16 Kerberos service tickets in the window of offense 50." → `ev_ver05_tickets` (`SELECT sourceip, username, QIDNAME(qid) AS event_name FROM events WHERE username = 'branch.user05' AND qid = 5000938 LIMIT 100 START 1791567443865 STOP 1791567445865`, 16 satır)
   - claim 1: "All 16 tickets were logged by the log source with ID 213." → `ev_ver05_source` (`SELECT logsourceid, COUNT(*) AS tickets FROM events WHERE username = 'branch.user05' AND qid = 5000938 GROUP BY logsourceid LIMIT 100 START 1791567443865 STOP 1791567445865`, 1 satır)
   - claim 2: "The account branch.user05 also logged on successfully from 192.0.2.98 in the same window." → `ev_ver05_wrong` (excerpt `[{"sourceip":"192.0.2.98","username":"branch.user05","event_name":"Success Audit: An account was successfully logged on"}]`, 1 satır; sorgu `... AND qid = 5000830 ...`)
   - `expect: {agrees: false, disputed_claims: [2]}`

Kanıt satırları Verification profilinin süzgecinden geçecek sütunlardır (payload ve serbest metin yok); `request_status` gibi payload alanlarına dayanan claim yazılmaz.

## Adımlar

1. Kayıtlardan değerleri okuyan bir betik (scratch dizininde, repoya girmez; `gzip` + `json`): offense alanları, Verification'ın doğru kanıt satırları. Betik PR'a kod bloğu olarak girer.
2. Dört Investigation, üç Verification senaryosu.
3. README'lerin senaryo tablolarına birer satır (`inv-01`'in satırı gibi: senaryo, kayıt, ne istediği bir cümle).
4. `test_eval_replay_suites.py`: yeni sabitler (`INV_03`…`INV_06`, `VER_03`…`VER_05`), planlanan listeler, testler (aşağıda).
5. `harness/README.md`'nin suite tablosunda sayılar.

## Kabul kriterleri ve testler

Testler gerçek model, lab ya da ağ kullanmaz.

1. **Planlanan senaryolar.** `test_the_gold_suites_are_quality_suites_with_the_planned_scenarios` yeni listelerle: Investigation `[INV_01 … INV_06]`, Verification `[VER_01 … VER_05]`, ikisi de `quality`.
2. **Scripted model k = 2.** `test_every_gold_scenario_passes_with_a_scripted_model_k_2` yeni senaryolarla geçer (bütün koşular `pass`, `ungrounded_evidence` 0).
3. **Kayıtlar beklentiyi taşır.** `test_the_new_gold_scenarios_name_events_their_recordings_hold`: her yeni Investigation senaryosunun `check_files`'ı geçer (kayıt var, `find_events`'in her biri kayıtta, skill `skills/`'te).
4. **Engellenmiş saldırı `fp` olamaz.** `test_a_blocked_attack_called_fp_fails`: `inv-04`'e `verdict: fp` cevabı → yalnızca `verdict_in` düşer.
5. **Onaylı tarayıcı `tp` olamaz.** `test_the_approved_scanner_called_tp_fails`: `inv-05`'e `verdict: tp` → yalnızca `verdict_in` düşer.
6. **Yanlış claim'e katılmak düşer.** `test_agreeing_with_the_wrong_attack_family_fails` (`ver-03`, `agrees: True, disagreements: []` → `agrees` ve `disputed_claims` düşer) ve `test_agreeing_with_an_invented_logon_fails` (`ver-05`, aynı).
7. **Doğru kanıt kayda uyar.** `test_the_verification_excerpts_match_their_recordings`: `ev_ver03_xss`, `ev_ver04_requests`, `ev_ver04_source`, `ev_ver05_tickets`, `ev_ver05_source`'un excerpt satırları, sorgunun koşuluna uyan kayıt event'lerinden aynı sütunlarla yeniden hesaplanır ve eşittir (sıra önemsiz); `rows` satır sayısıdır.

## Kontroller

```bash
uv run ruff check && uv run ruff format --check && uv run pyright
uv run pytest harness -q
uv run lint-imports
uv run pytest -q                  # tam suite, ~8 dk; PR'dan önce zorunlu
git grep -I -n -i -E 'openai|openrouter|deepseek|anthropic|qwen|glm|zhipu|space-bunny|opencode' -- packages services apps prompts harness skills ':(exclude)packages/agents/src/ais0c_agents/llm.py' ':(exclude)packages/agents/pyproject.toml'   # boş dönmeli
```

Tam suite'te Temporal test sunucusu takılırsa (%0 CPU, 10 dakikadan uzun) pytest'i PID ile durdur ve `-v` ile yeniden koş.

## PR

`../ais0c-prs/PR-T-080.md`, `.github/pull_request_template.md` biçiminde: kriter başına test adı, betik, kontrollerin sonuçları.

## Durma noktaları

- Kayıtta bu dosyada yazanla uyuşmayan bir olgu: kayıt doğrudur; dur ve PR'da yaz, beklentiyi kendin değiştirme.
- Scripted model ya da `check_files` bir senaryoyu reddediyor ve izinli dosyalarla düzelmiyor: dur ve PR'da yaz.
- `ver-04`'ün `GROUP BY` sorgusu replay'in AQL motorunda çalışmıyor: kanıtın sorgusu modelle çalıştırılmaz (yalnızca metindir), ama motor sorguyu reddeden bir denetim yapıyorsa dur ve PR'da yaz.

## Notlar

- Gerçek model komutu koşma. Ölçümü planner yapar.
- Effort: orta.
