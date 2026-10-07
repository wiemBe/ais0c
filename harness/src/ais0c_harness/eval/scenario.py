"""What every scenario has, whatever agent it runs against (T-030 criterion 1).

A scenario is one YAML file in its suite's directory, named after its `id`. The common fields
are `id`, `suite`, `agent`, `title`, `description`, `input` and `expect`; each agent's scenario
model (triage.py) defines its `input` and `expect`. Every `expect` may name the tools the run
must call at least once (`required_tools`) and a ceiling on its tool calls (`max_tool_calls`).
Unknown fields are rejected. The checks that need the suite or the agent's profile are in
suites.py.

These are plain Pydantic models, not contract models: contract models are defined only in
packages/contracts.
"""

from collections.abc import Iterator
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

SCENARIO_ID_PATTERN: Final = r"^[a-z][a-z0-9]*-[0-9]{2}-[a-z0-9]+(?:-[a-z0-9]+)*$"
# The run IDs (`harness-<id>-<n>-retry`) must stay within the contract's 200 characters.
SCENARIO_ID_MAX_LENGTH: Final = 100

ScenarioId = Annotated[
    str, StringConstraints(pattern=SCENARIO_ID_PATTERN, max_length=SCENARIO_ID_MAX_LENGTH)
]
SuiteId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,62}$")]
AgentId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,62}$")]
ToolId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class Expectation(BaseModel):
    """What every agent's expectation may hold besides its own fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_tools: frozenset[ToolId] = frozenset()
    """Tools the run must call at least once; a call the gateway denied does not count."""
    max_tool_calls: Annotated[int, Field(ge=0)] | None = None
    """The most tool calls the run may make, denied calls included."""


class ScenarioBase(BaseModel):
    """The fields every scenario has. Each agent's model adds `input` and `expect`."""

    model_config = ConfigDict(extra="forbid")

    id: ScenarioId
    suite: SuiteId
    agent: AgentId
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    description: Annotated[str, StringConstraints(min_length=1)]

    def expectation(self) -> Expectation:
        """The scenario's `expect`."""
        raise NotImplementedError

    def scripted_tools(self) -> frozenset[str]:
        """The tools the scenario has results for."""
        raise NotImplementedError


def strings(value: JsonValue) -> Iterator[str]:
    """Every string inside a JSON value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
