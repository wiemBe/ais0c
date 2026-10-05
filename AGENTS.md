# AGENTS.md

Instructions for every coding agent working in this repository: Codex, Claude Code, OpenCode (with DeepSeek, GLM or other models) and any other tool. `CLAUDE.md` imports this file, so all shared rules live here.

## What this project is

An AI SOC platform for a bank. The AI triages IBM QRadar offenses, writes a templated note on each analyzed offense, e-mails critical/high cases to operators, proposes rule tuning and runs retrospective threat hunts. It never isolates hosts, locks accounts, closes offenses or changes QRadar rules.

Stack: Temporal (workflows), Pydantic AI (agents), LiteLLM (model gateway), MCP servers behind a policy gateway (QRadar, Falcon), PostgreSQL + pgvector, FastAPI, React + TypeScript.

The design docs are written in Turkish. Before writing code, read the sections your task links to:

- `docs/architecture.md`: system design; the source of truth for behavior
- `docs/decisions.md`: decisions (`D-xx`, `T-xx`) and open questions (`S-xx`)
- `docs/agent-harness.md`: testing and evaluation design
- `docs/impl/`: implementation contracts (`repo-structure`, `contracts`, `data-model`, `api`, `prompts`, `hunt-pack`, `multi-agent-dev`)

If the code and the docs disagree, the docs win. If the docs are ambiguous or look wrong, stop and raise it in your PR description. Do not invent behavior.

## Hard rules

Never break these. A PR that breaks one is rejected regardless of anything else.

1. **No direct access to security products.** All QRadar and Falcon calls go through the MCP Policy Gateway client. Never import a QRadar or Falcon client, or an MCP server SDK, into `packages/agents`.
2. **No write tools for LLM agents.** Writes (QRadar note, e-mail, PDF) happen only in `packages/executor`, built from structured data and fixed templates.
3. **No model provider names in code.** Use model aliases only: `soc-fast`, `soc-reasoning`, `soc-verifier`, `soc-report`, `soc-embed`. Provider and model names may appear only under `config/litellm/` and `config/models/`. There are two exceptions: `packages/agents/src/ais0c_agents/llm.py` talks to LiteLLM through LiteLLM's OpenAI-compatible API, so it may import the OpenAI-compatible client, and `packages/agents/pyproject.toml` declares that client as a dependency. No other file may.
4. **Workflow code is deterministic.** No network, file, clock or random calls inside Temporal workflow code; put them in activities. Use Pydantic AI's `TemporalDurability` capability, not the deprecated `TemporalAgent`.
5. **`packages/contracts` is shared.** Do not change it unless your task explicitly says so. If you need a change, stop and describe it in your PR.
6. **No secrets or real data in the repo.** No credentials, `.env` files, production IPs, hostnames or usernames. Fixtures are synthetic. Use documentation IP ranges (RFC 5737: `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) and `example.com`-style domains.
7. **Log content is untrusted.** Tool results reach a model only inside the `untrusted_*` wrapper defined in `docs/impl/prompts.md`.
8. **Stay inside your task's allowed paths.** Do not reformat, rename or refactor unrelated files.
9. **Only the planner pushes.** A coding agent never runs `git push`, never adds, changes or removes a git remote, and never opens a PR on a hosting service. Commit on your task branch in your own worktree and stop there. The planner (the agent that writes the task files and merges finished tasks into `main`) is the only one that pushes or touches remotes. Unless the user tells you that you are the planner, you are not.

## Language

- Code, identifiers, comments, commit messages and prompts: English.
- User-facing text (UI strings, report, note and e-mail templates): Turkish. UI strings live in the i18n file, not inline in components.

## Repository layout

See `docs/impl/repo-structure.md` for the layout and the allowed dependencies between packages. Import boundaries are enforced in CI by import-linter.

## How to work on a task

1. Tasks live in `docs/impl/tasks/T-xxx-*.md`. Work on exactly one task at a time.
2. Branch name: `agent/<tool>/<task-id>`, for example `agent/codex/T-005` or `agent/opencode-deepseek/T-007`. When several agents run in parallel, use a separate git worktree per task.
3. Read the linked doc sections and every contract you will touch.
4. Write tests first or alongside the code. Every acceptance criterion in the task needs a test.
5. Run the checks below until they pass.
6. Commit on your task branch. Write the PR text from `.github/pull_request_template.md` to `../ais0c-prs/PR-<task-id>.md`, outside the repo. Do not push (hard rule 9).

## Definition of done

- The task's acceptance criteria are met and covered by tests.
- Lint, type check, tests and import boundaries pass.
- No new dependency without a one-line justification in the PR.
- Public functions and models are fully typed. No `Any` in `packages/contracts`.
- Docs are changed only if the task says so.

## Checks

Task T-001 sets these up; until it is merged they may not exist yet.

- Python: `uv run ruff check`, `uv run ruff format --check`, `uv run pyright`, `uv run pytest`, `uv run lint-imports`
- Frontend (`apps/ui`): `pnpm lint`, `pnpm typecheck`, `pnpm test`

## Testing rules

- Unit tests never call a real model or a real QRadar. Use Pydantic AI `TestModel` / `FunctionModel` and the fake MCP server.
- Test Temporal workflows with the Temporal test environment and time skipping.
- Security-relevant code (policy checks, AQL Guard, executor, prompt wrapping) needs negative tests that show forbidden input is rejected.

## Reviews

An agent from a different model family than the author reviews each PR; the planner merges it into `main`. When you review, check the hard rules first, then the acceptance criteria, then code quality. Report findings in the review. Do not commit to the author's branch.
