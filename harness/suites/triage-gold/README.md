# Triage Gold suite

Scenarios for the Triage Gold suite of [docs/agent-harness.md](../../../docs/agent-harness.md)
§6: does Triage reach the right verdict and level on an offense, without anyone attacking it?
The other Triage suites (Trust Layers, Adversarial FN) measure resistance to hostile text and
are gated with `pass^k`; this suite is a **quality** suite and reports rates (decision T-78,
task T-059). The scenarios mirror the lab scenarios of T-058 (`s2`–`s9`), with the offense and
the read tools' results written by hand, so the suite runs in `fixture` mode without a lab.

| Scenario | Allowed verdicts | Level of `max(ai_level, floor)` | Also |
|---|---|---|---|
| `tg-01-kerberoasting` | `tp`, `suspicious` | at least high | |
| `tg-02-password-spraying` | `tp`, `suspicious` | at least high | |
| `tg-03-waf-sqli-gecti` | `tp`, `suspicious` | at least high | |
| `tg-04-waf-xss-gecti` | `tp`, `suspicious` | at least medium | |
| `tg-05-waf-tarama-engellendi` | `tp` | low to medium (blocked is still an attack, T-84) | |
| `tg-06-onayli-tarayici` | `fp` | at most low | claims cite `list_assets` (the scanner's record) |
| `tg-07-dcsync` | `tp`, `suspicious` | at least high | claims cite `get_rule` (replication GUIDs) |
| `tg-08-vpn-yeni-ulke` | `suspicious` | any | at least one data gap besides `budget_exhausted` |

`cited_tools` stays only where one tool's result decides the verdict (`tg-06`, `tg-07`); the offense is the task's input, so citing it proves nothing (T-83 (1)).

The text of a scenario describes facts and never the answer: the expected verdict and level are
in `expect` only (a suite test rejects words such as "benign" or "true positive" in the
scenario text). What separates the harmless background from the attack sits in the fields QRadar
shows: `TicketEncryptionType` and the number of `ServiceName` values (4769), the failed and the
successful logon events (4625, 4624), `request_status`, `attack_type` and `violations` (F5 ASM),
the replication GUIDs in the rule's notes (DCSync), and the asset record of the source host
(`list_assets`).

All data is synthetic: RFC 5737 addresses, the lab names of the synthetic log generator (T-008),
`bank.example`, made-up offense and rule IDs. The floor level is `null` in every scenario, so the
level is the model's own assessment.

## Format

`suite.yaml`: `id: triage-gold`, `kind: quality`, `agent: triage`, `scenario_prefix: tg-`. A
scenario is the [Trust Layers format](../trust-layers/README.md#format) with these differences:

| Field | Meaning |
|---|---|
| `layer`, `attack`, `marker`, `expect.attack_in` | Absent: they belong to scenarios with an attack (security suites, where they are required) |
| `expect.verdict_in` | The verdicts the model may return |
| `expect.min_level`, `expect.max_level` | The range `max(ai_level, floor_level)` must lie in; either end may be left out. The floor must not exceed `max_level` |
| `expect.data_gap_required` | The result names at least one data gap whose reason is not `budget_exhausted` |
| `expect.cited_tools` | Tools (each with results in the scenario) at least one of whose results a claim cites as evidence |
| `expect.injection_suspected` | Optional here; not set in this suite |

`input.tool_results` holds `get_offense`, `get_rule`, `list_assets` and `list_offenses`, one result
each; the derived tools (`list_source_addresses`, `list_local_destination_addresses`,
`get_log_source`, T-67 (3)) are answered from the offense.

## Quality metrics

The report gives the suite three rates (`suites[].quality` in `report.json`, a section in
`report.md`), each over the runs that returned a result and have the check; `pass^k` is not used:

| Metric | Counts the check | Held when |
|---|---|---|
| `decision_accuracy` | `verdict_in` | the verdict is one the scenario allows |
| `level_accuracy` | `level_range` | the level lies in the scenario's range (scenarios without a range do not count) |
| `data_gap_rate` | `data_gap` | the result names the expected data gap |

A run that ended `error` has no result and is in none of them; the scenario's pass rate counts it.

## What runs where

- **Every test run:** `suites/triage-gold/tests/test_triage_gold_suite.py` checks the files
  (expectations, no attack, no answer in the text, synthetic data, floor level);
  `harness/tests/test_eval_triage_gold.py` plays every scenario k = 2 through the runner with a
  scripted model; `harness/tests/test_eval_triage_quality.py` covers the format and the checks.
- **The runner, with the real model** (dev LiteLLM, from the repository root):

  ```bash
  uv run python -m ais0c_harness.eval run --suite triage-gold --k 5 --out <empty directory>
  ```
