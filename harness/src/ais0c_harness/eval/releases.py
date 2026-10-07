"""Changed model releases (T-030 criterion 10, T-016 criterion 5, decision T-24).

`model_release_changes` (ais0c_activities.model_release) lists the aliases whose release in the
model registry differs from the one their last agent run recorded in `agent_runs`. Each is a
new model release, so the model gate (B2) must run again for the agents that use the alias,
with those agents' suites. describe_release_changes turns the list into that instruction.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ais0c_activities.model_release import ModelReleaseChange
from ais0c_harness.eval.suites import Suite


@dataclass(frozen=True)
class AgentSuites:
    agent_id: str
    suites: tuple[str, ...]
    """The harness suites that run against the agent."""


def agents_by_alias(
    aliases: Mapping[str, str], suites: Sequence[Suite]
) -> dict[str, list[AgentSuites]]:
    """Alias -> the agents whose manifest uses it, each with its harness suites.

    `aliases` is agent ID -> model alias, as the manifests under config/agents/ set it.
    """
    found: dict[str, list[AgentSuites]] = {}
    for agent_id, alias in sorted(aliases.items()):
        own = tuple(suite.id for suite in suites if suite.agent == agent_id)
        found.setdefault(alias, []).append(AgentSuites(agent_id=agent_id, suites=own))
    return found


def describe_release_changes(
    changes: Sequence[ModelReleaseChange], agents: Mapping[str, Sequence[AgentSuites]]
) -> str:
    """What changed for each alias and which suites the model gate must run with."""
    if not changes:
        return "No model release changed since the last recorded agent runs.\n"
    lines: list[str] = []
    for change in changes:
        lines.append(f"{change.alias}: {change.describe()}")
        users = agents.get(change.alias, ())
        if not users:
            lines.append("  no agent manifest uses this alias")
        for user in users:
            if user.suites:
                options = " ".join(f"--suite {suite_id}" for suite_id in user.suites)
                lines.append(f"  {user.agent_id}: run the model gate with {options}")
            else:
                lines.append(f"  {user.agent_id}: no harness suite runs against this agent yet")
    lines += [
        "",
        "Run the suites with the new release and compare the report with the last baseline:",
        "  python -m ais0c_harness.eval run <suites> --registry <file> --out <dir>",
        "  python -m ais0c_harness.eval gate --baseline <report.json> --candidate <dir>/report.json",
    ]
    return "\n".join(lines) + "\n"
