# Investigation gold suite

A quality suite (the pass rate counts, no `pass^k`) for the Investigation agent, played in mode
`replay` on a recorded lab offense ([harness/README.md](../../README.md#replay-recorded-lab-offenses)).
Task T-052 writes it; T-055 adds the skill suites and the budget measurements.

| Scenario | Recording | What it asks |
|---|---|---|
| `inv-01-dcsync` | `lab-30-dcsync` | The DCSync offense of the lab: `svc_backup` asked a domain controller for directory replication three times, from three servers. Find all three; keep the decision at `suspicious` or raise it to `tp`; cite only evidence the gateway returned |
| `inv-02-dcsync-no-skill` | `lab-30-dcsync` | The same objective and evidence without a selected skill, for the paired skill/no-skill measurement |

## Format

The scenario is `agent: investigation`. `ReplayInput` and `InvestigationScenario`
([investigation.py](../../src/ais0c_harness/eval/investigation.py)) read it; unknown fields are
rejected, and the suite refuses a scenario whose recording is missing or does not hold the expected
events.

| Field | Meaning |
|---|---|
| `id`, `suite`, `agent`, `title`, `description` | as every scenario ([scenario.py](../../src/ais0c_harness/eval/scenario.py)) |
| `input.recording` | A directory under `harness/recordings/`: the offense, the enrichment, the events and the read tools come from it |
| `input.objective` | The plan step's objective: the task's `objective` (the Orchestrator's sentence) |
| `input.triage` | What Triage handed over, without its rationale: `verdict`, `confidence`, `ai_level`, `investigation_focus`, `claims`, `data_gaps` |
| `input.context_evidence` | The `EvidenceRef`s the claims cite; the agent sees them as `ev_c<n>`, and may cite them |
| `input.skill` | Optional: `{id, version}` of a skill under `skills/` (loaded in dev mode, drafts allowed) |
| `input.evaluated_at` | Optional: the moment of the evaluation; default the offense's last update plus 5 minutes. The task's window is `evaluation_window(offense, evaluated_at)`; `LAST n` counts back from it |
| `expect.verdict_in` | Verdicts the agent may return |
| `expect.find_events` | Events the run must find: `{address, username}`. An urgent event candidate names it (source or destination, and user), or a claim or timeline entry cites evidence whose rows hold both |
| `expect.required_tools`, `expect.max_tool_calls` | as in every scenario |

The task is built by the worker's own function (`investigation_task`), the agent by
`build_investigation_agent` with the profile `qradar-investigate-read`. The addresses in a
scenario are the recording's anonymized ones; the evidence the scenario hands over is made up as
Triage would hand it over.

```bash
uv run python -m ais0c_harness.eval run --suite investigation-gold --k 5 --out <empty directory>
```
