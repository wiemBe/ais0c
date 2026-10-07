# Verification gold suite

A quality suite (the pass rate counts, no `pass^k`) for the Verification agent, played in mode
`replay` on a recorded lab offense ([harness/README.md](../../README.md#replay-recorded-lab-offenses)).
Task T-052 writes it; T-055 adds the budget measurements.

| Scenario | Recording | What it asks |
|---|---|---|
| `ver-01-refutable-ip` | `lab-30-dcsync` | Three claims of a `tp` decision; one names a source address (`192.0.2.250`) that no event of the account shows. The verifier contests that claim and only that one: `agrees: false` |
| `ver-02-all-correct` | `lab-30-dcsync` | Three claims that hold. The verifier checks them at the source and agrees: `agrees: true` |

## Format

The scenario is `agent: verification`. `VerificationInput` and `VerificationScenario`
([verification.py](../../src/ais0c_harness/eval/verification.py)) read it; unknown fields are
rejected. `input.recording`, `input.objective` and `input.evaluated_at` are as in the
[Investigation suite](../investigation-gold/README.md#format).

| Field | Meaning |
|---|---|
| `input.reviewed` | The decision under review: `verdict`, `confidence`, `ai_level` |
| `input.claims` | The claims (`text`, `evidence_ids`); two claims may not share a text, a disagreement names a claim by its text |
| `input.evidence` | The `EvidenceRef`s the claims cite; their windows decide the task's window (decision T-56) |
| `input.critical` | Whether the workflow marks the claims critical |
| `expect.agrees` | The `agrees` the verifier must return |
| `expect.disputed_claims` | Positions in `input.claims` of the claims the verifier must contest; empty exactly when `agrees` is true |

The task is built by the worker's own function (`verification_task`), the agent by
`build_verification_agent` with the profile `qradar-verify-read`: a two hour query window, 200
rows, and the output filter that drops payload and free-text fields.

```bash
uv run python -m ais0c_harness.eval run --suite verification-gold --k 5 --out <empty directory>
```
