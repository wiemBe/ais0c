"""Suites and their scenarios on disk (T-030 criterion 1).

A suite is a directory under harness/suites/ with a `suite.yaml`:

    id: trust-layers          # the directory's name
    title: Trust Layers
    kind: security            # security: pass^k is a hard gate; quality: the pass rate counts
    agent: triage             # the agent every scenario of the suite runs against
    scenario_prefix: tl-      # every scenario ID starts with it

Every other `*.yaml` file in the directory is one scenario, named after its `id`. A scenario is
rejected when a field is unknown or invalid, its file name is not its `id`, its `id` lacks the
suite's prefix, its `suite` is not the directory, its `agent` is not the suite's, or it has
results for (or requires) a tool that is not in the agent's gateway profile.

A scenario's version is the sha256 of its file's bytes, followed by the manifest of the recording
it names, if it names one. A suite's version is the sha256 of
`suite.yaml`'s bytes followed by the list of its scenarios' (id, version) pairs as JSON.

Triage has had an adapter since T-030. T-052 adds replay of recorded lab answers and the
Investigation and Verification adapters; T-053 adds the Orchestrator, the Reporting agent and the
Turkish Quality suite, which runs the Reporting agent under its own evaluator. A suite for an
agent without an adapter fails with "not supported yet".
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Annotated, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from ais0c_harness.eval.adapter import AgentAdapter
from ais0c_harness.eval.config import GATEWAY_CONFIG_DIR, GATEWAY_CONNECTORS
from ais0c_harness.eval.investigation import InvestigationAdapter
from ais0c_harness.eval.orchestrator import OrchestratorAdapter
from ais0c_harness.eval.reporting import ReportingAdapter
from ais0c_harness.eval.scenario import AgentId, ScenarioBase, SuiteId
from ais0c_harness.eval.triage import TriageAdapter
from ais0c_harness.eval.turkish import TurkishQualityAdapter
from ais0c_harness.eval.verification import VerificationAdapter
from ais0c_mcp_gateway.registry import RegistryError, load_registry

SUITES_DIR: Final = "harness/suites"
SUITE_FILE: Final = "suite.yaml"
ADAPTERS: Final[Mapping[str, type[AgentAdapter]]] = {
    adapter.suite_agent: adapter
    for adapter in (
        TriageAdapter,
        InvestigationAdapter,
        VerificationAdapter,
        OrchestratorAdapter,
        ReportingAdapter,
        TurkishQualityAdapter,
    )
}
"""The agents the harness can run, keyed by the `agent` a suite.yaml names."""

SuiteKind = Literal["security", "quality"]


class SuiteError(ValueError):
    """A suite or scenario file is invalid; the message names the file and the reason."""


class SuiteDefinition(BaseModel):
    """suite.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: SuiteId
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    kind: SuiteKind
    agent: AgentId
    scenario_prefix: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*-$")]


@dataclass(frozen=True)
class ScenarioFile:
    path: Path
    version: str
    scenario: ScenarioBase

    @property
    def id(self) -> str:
        return self.scenario.id


@dataclass(frozen=True)
class Suite:
    path: Path
    definition: SuiteDefinition
    version: str
    scenarios: tuple[ScenarioFile, ...]

    @property
    def id(self) -> str:
        return self.definition.id

    @property
    def kind(self) -> SuiteKind:
        return self.definition.kind

    @property
    def agent(self) -> str:
        return self.definition.agent


def adapter_type(agent: str) -> type[AgentAdapter]:
    """The adapter of the agent a suite.yaml names; SuiteError when the harness cannot run it."""
    adapter = ADAPTERS.get(agent)
    if adapter is None:
        raise SuiteError(
            f"agent {agent!r} is not supported yet: the harness runs {', '.join(sorted(ADAPTERS))}"
        )
    return adapter


def manifest_agent(agent: str) -> str:
    """The agent the manifest of `agent`'s adapter builds; what a model alias is recorded for."""
    return adapter_type(agent).agent_id


def load_suites(root: Path, ids: Sequence[str] | None = None) -> list[Suite]:
    """The suites under `root`/harness/suites, by directory name; only `ids` when given.

    Raises SuiteError for an invalid suite or scenario, and for an unknown ID in `ids`.
    """
    directory = root / SUITES_DIR
    found = sorted(path.parent for path in directory.glob(f"*/{SUITE_FILE}"))
    if ids is not None:
        names = {path.name for path in found}
        if unknown := [suite_id for suite_id in ids if suite_id not in names]:
            raise SuiteError(f"no suite {', '.join(unknown)} under {directory}")
        found = [directory / suite_id for suite_id in dict.fromkeys(ids)]
    return [load_suite(path, root=root) for path in found]


def load_suite(directory: Path, *, root: Path) -> Suite:
    definition = _definition(directory)
    scenarios = tuple(
        _scenario(path, definition, root=root)
        for path in sorted(directory.glob("*.yaml"))
        if path.name != SUITE_FILE
    )
    digest = hashlib.sha256((directory / SUITE_FILE).read_bytes())
    pairs = [[item.id, item.version] for item in sorted(scenarios, key=lambda item: item.id)]
    digest.update(json.dumps(pairs, separators=(",", ":")).encode("utf-8"))
    return Suite(
        path=directory, definition=definition, version=digest.hexdigest(), scenarios=scenarios
    )


def load_scenario(path: Path, *, root: Path) -> ScenarioFile:
    """One scenario, checked against the suite.yaml next to it."""
    return _scenario(path, _definition(path.parent), root=root)


def profile_tools(root: Path, agent: str) -> frozenset[str]:
    """The read tools of the gateway profile that `agent`'s manifest names."""
    return _profile_tools(root.resolve(), adapter_type(agent).manifest_path)


@cache
def _profile_tools(root: Path, manifest_path: str) -> frozenset[str]:
    path = root / manifest_path
    try:
        manifest: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        registry = load_registry(root / GATEWAY_CONFIG_DIR, GATEWAY_CONNECTORS)
    except (OSError, yaml.YAMLError, RegistryError) as error:
        raise SuiteError(f"cannot read the agent's profile: {error}") from error
    name = manifest.get("toolset_profile") if isinstance(manifest, dict) else None
    if name is None:
        # The Orchestrator and the Reporting agent have no tools (their manifests say so).
        return frozenset()
    profile = registry.profiles.get(name)
    if profile is None:
        raise SuiteError(f"{path} names no gateway profile")
    return frozenset(tool.id for tool in profile.tools.values() if tool.risk == "read")


def _definition(directory: Path) -> SuiteDefinition:
    path = directory / SUITE_FILE
    data = _read(path)
    try:
        definition = SuiteDefinition.model_validate(data)
    except ValidationError as error:
        raise SuiteError(f"{path}: {error}") from None
    if definition.id != directory.name:
        raise SuiteError(f"{path}: id {definition.id!r} is not the directory's name")
    adapter_type(definition.agent)
    return definition


def _scenario(path: Path, suite: SuiteDefinition, *, root: Path) -> ScenarioFile:
    data = _read(path)
    if not isinstance(data, dict):
        raise SuiteError(f"{path}: a scenario is a mapping")
    agent = data.get("agent")
    if agent != suite.agent:
        raise SuiteError(f"{path}: agent {agent!r} is not the suite's agent {suite.agent!r}")
    try:
        scenario = adapter_type(suite.agent).scenario_type.model_validate(data)
    except ValidationError as error:
        raise SuiteError(f"{path}: {error}") from None
    if scenario.id != path.stem:
        raise SuiteError(f"{path}: id {scenario.id!r} is not the file's name")
    if not scenario.id.startswith(suite.scenario_prefix):
        raise SuiteError(f"{path}: id {scenario.id!r} lacks the prefix {suite.scenario_prefix!r}")
    if scenario.suite != suite.id:
        raise SuiteError(f"{path}: suite {scenario.suite!r} is not the directory {suite.id!r}")
    tools = profile_tools(root, suite.agent)
    if unknown := sorted(scenario.scripted_tools() - tools):
        raise SuiteError(f"{path}: results for {', '.join(unknown)}, not in the agent's profile")
    if unknown := sorted(scenario.expectation().required_tools - tools):
        raise SuiteError(f"{path}: requires {', '.join(unknown)}, not in the agent's profile")
    try:
        scenario.check_files(root)
    except ValueError as error:
        raise SuiteError(f"{path}: {error}") from None
    digest = hashlib.sha256(path.read_bytes())
    for part in scenario.version_parts(root):
        digest.update(part)
    return ScenarioFile(path=path, version=digest.hexdigest(), scenario=scenario)


def _read(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise SuiteError(f"cannot read {path}: {error}") from None
