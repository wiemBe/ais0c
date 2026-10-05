"""Agents that run in workflow code, through Pydantic AI's TemporalDurability (decision T-02,
architecture §20).

With TemporalDurability an agent's run is workflow code: Pydantic AI turns each model request and
each tool call into an activity, so a worker restart resumes the run from its history. The agent
must be built before the worker starts, outside any workflow, so that its activities can be
registered; the worker builds it and installs its run here.

Workflow modules import this module passed through Temporal's sandbox. The sandbox reloads
workflow code for every run, but a passed-through module is the worker's own, so workflows reach
the agent the worker installed, not a copy.

The workflows package may not import the agents package (docs/impl/repo-structure.md); what it
knows of an agent is the protocols below, written in contract types.
"""

from collections.abc import Awaitable
from typing import Protocol

from ais0c_contracts import (
    AgentTask,
    EnrichmentContext,
    OffenseSnapshot,
    RunStatus,
    TriageResult,
    Usage,
)


class TriageRunReport(Protocol):
    """How a Triage agent run ended; `ais0c_agents.AgentRun` has these fields."""

    @property
    def status(self) -> RunStatus: ...

    @property
    def result(self) -> TriageResult | None:
        """Set only when the status is `completed`."""
        ...

    @property
    def usage(self) -> Usage: ...

    @property
    def error(self) -> str | None:
        """Why the run did not complete; for logs, never shown to a model."""
        ...


class TriageAgentRun(Protocol):
    """One run of the Triage agent, called from workflow code.

    `nonce` is the run's `untrusted_*` tag suffix (docs/impl/prompts.md). The call must be
    deterministic apart from the activities Pydantic AI starts for it.
    """

    def __call__(
        self,
        task: AgentTask,
        offense: OffenseSnapshot,
        enrichment: EnrichmentContext,
        *,
        nonce: str,
    ) -> Awaitable[TriageRunReport]: ...


_triage_agent: TriageAgentRun | None = None


def install_triage_agent(run: TriageAgentRun) -> None:
    """Make `run` the Triage agent of every TriageWorkflow this process executes.

    Called by the worker before it starts; a later call replaces the agent.
    """
    global _triage_agent
    _triage_agent = run


def triage_agent() -> TriageAgentRun:
    """The installed Triage agent.

    Raises RuntimeError when none is installed: the worker is misconfigured, and the workflow
    task fails until a worker with the agent picks it up.
    """
    if _triage_agent is None:
        raise RuntimeError("no Triage agent is installed in this worker")
    return _triage_agent
