# Skills

A skill is a versioned method for investigating one kind of event, such as DCSync
([architecture §7](../docs/architecture.md), "Skill'ler"; decision T-21). It tells an agent how to
investigate and never adds tools or permissions: an agent's permissions are its toolset, the
workflow policy and the gateway policy. There is no registry service; the registry is the
approved skills in this directory.

The loader, the router and the checks below are `ais0c_knowledge.skills` in `packages/knowledge`.

## Layout

```text
skills/<id>/<version>/skill.yaml        the manifest
skills/<id>/<version>/instructions.md   English instructions; they become part of the prompt
```

A version directory holds exactly these two files, and a skill runs no scripts. The directory
names must equal the manifest's `id` and `version`. Symbolic links are refused, and files at the
top of `skills/`, such as this README, are not skills.

## Manifest

| Field | Meaning |
|---|---|
| `id` | Lowercase words joined by hyphens, e.g. `windows-dcsync` |
| `version` | `MAJOR.MINOR.PATCH` |
| `status` | `draft` or `approved` |
| `owner` | The person or team that maintains the skill |
| `allowed_agent_roles` | The agents that may use it, e.g. `[investigation]` |
| `triggers` | `rule_ids`, `log_source_types`, `attack_techniques`: when the router offers the skill |
| `required_telemetry` | Log source types and the events the method reads. Without a `required: true` source the agent reports a data gap instead of concluding; a `required: false` source only adds detail. |
| `required_evidence` | What the agent collects, or reports as a data gap, before it concludes; each item has an `id` |
| `budgets` | `tokens`, `tool_calls`, `wall_clock_seconds`: upper limits for one agent run that uses the skill |
| `output_schema` | The agent result model, e.g. `InvestigationResult` |
| `eval_suites` | The harness suites the skill passes before approval (T-030) |
| `expires_at` | A date; from 00:00 UTC on that day the skill is no longer offered |
| `content_hash` | Set on approval; `null` in a draft |
| `approved_by` | Set on approval; `null` in a draft |

Every field is required, and any other field is refused. A field that reads like a grant
(`tools`, `allowed_tools`, `permissions`, `toolset_profile`, ...) is refused with its own error
wherever it appears.

## Triggers and the router

The router (`candidate_skills`) offers a skill for an offense when any one trigger matches:

- `rule_ids`: one of the offense's rules. Rule IDs differ between QRadar installations, so they
  are filled in for the target QRadar before approval.
- `log_source_types`: the type of one of the offense's log sources. Such a trigger matches every
  offense from that type, so it suits only skills about one product's alerts.
- `attack_techniques`: one of the ATT&CK techniques the offense is tagged with, compared
  exactly: `T1110` does not match `T1110.003`.

Only the latest approved version of a skill is offered, and only when it has not expired and its
`allowed_agent_roles` include the agent's role. Drafts are never offered. The candidates come
sorted by skill ID.

## Instructions

`instructions.md` is English plain text: printable ASCII plus typographic punctuation. Headings
start at level 2 (`##`), since the prompt places the text under its own Skill section. The loader
refuses a skill whose instructions, or any text in its manifest,

- try to override the agent's rules ("ignore previous instructions", "you are now", ...);
- carry chat-template markers or role headers (`<|im_start|>`, `[INST]`, `System:`);
- contain something that reads as an `untrusted_*` or `org_context` tag;
- hide text from review: invisible or control characters, HTML comments, letters outside ASCII.

## Content hash

`content_hash` is `sha256:` and the SHA-256 of both files: `skill.yaml` without its
`content_hash:` line, then `instructions.md`, each as its name, its length in bytes and its UTF-8
bytes, with line endings turned into LF. Everything else in `skill.yaml` counts, comments
included, and so the same files give the same hash on every machine.

## Approval

Until the dual-control flow (T-033), a skill is approved in pull request review:

1. The suites in `eval_suites` pass (T-030).
2. The reviewer sets `status: approved` and `approved_by`, and leaves `content_hash: null`.
3. `uv run python -m ais0c_knowledge.skills hash skills/<id>/<version>` prints the hash, which
   goes into `content_hash`.
4. `uv run python -m ais0c_knowledge.skills check --mode prod` loads every skill as production
   does.

From then on the version is never edited: any change, even to the expiry date or a comment, is a
new version directory. The loader refuses an approved skill whose files no longer match its
`content_hash`. In production drafts are not loaded at all.
