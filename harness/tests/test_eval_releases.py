"""Changed model releases (T-030 criterion 10, T-016 criterion 5): the text is a pure function;
the command reads the changes through `model_release_changes`, which a test double replaces
(the database path is tested in packages/activities)."""

import io
from collections.abc import Mapping

from ais0c_activities.db import SessionFactory
from ais0c_activities.model_release import ModelReleaseChange, load_model_releases
from ais0c_contracts import ModelRelease
from ais0c_harness.eval import AgentSuites, agents_by_alias, describe_release_changes, load_suites
from ais0c_harness.eval.cli import Dependencies, main
from ais0c_harness.eval.config import agent_aliases

from .eval_helpers import REGISTRY, REPO_ROOT

DATABASE = {"AIS0C_DATABASE_URL": "postgresql+psycopg://harness:harness@127.0.0.1:1/ais0c"}


def change(alias: str = "soc-fast") -> ModelReleaseChange:
    current = load_model_releases(REGISTRY)[alias]
    recorded = current.model_copy(update={"engine_version": "0.11.0", "quantization": "fp8"})
    return ModelReleaseChange(alias=alias, recorded=recorded, current=current)


def test_the_agents_of_each_alias_come_from_the_manifests() -> None:
    aliases = agent_aliases(REPO_ROOT)

    assert aliases == {
        "investigation": "soc-reasoning",
        "orchestrator": "soc-reasoning",
        "reporting": "soc-report",
        "triage": "soc-fast",
        "verification": "soc-verifier",
    }
    agents = agents_by_alias(aliases, load_suites(REPO_ROOT))
    assert agents["soc-fast"] == [
        AgentSuites(agent_id="triage", suites=("adversarial-fn", "trust-layers"))
    ]
    assert agents["soc-reasoning"] == [
        AgentSuites(
            agent_id="investigation",
            suites=("investigation-gold", "skill-windows-dcsync"),
        ),
        AgentSuites(agent_id="orchestrator", suites=("orchestrator-gold",)),
    ]
    assert agents["soc-report"] == [
        AgentSuites(agent_id="reporting", suites=("reporting-gold", "turkish-quality"))
    ]
    assert agents["soc-verifier"] == [
        AgentSuites(agent_id="verification", suites=("verification-gold",))
    ]


def test_a_change_names_its_fields_and_the_suites_the_gate_must_run() -> None:
    changed = change()
    agents = {
        "soc-fast": [AgentSuites(agent_id="triage", suites=("adversarial-fn", "trust-layers"))]
    }

    text = describe_release_changes([changed], agents)

    assert text.startswith(f"soc-fast: {changed.describe()}\n")
    assert "engine_version: '0.11.0' -> None" in text
    assert "quantization: 'fp8' -> None" in text
    assert "  triage: run the model gate with --suite adversarial-fn --suite trust-layers" in text
    assert "python -m ais0c_harness.eval gate --baseline" in text


def test_an_agent_without_a_suite_and_an_unused_alias_are_named() -> None:
    agents = {"soc-reasoning": [AgentSuites(agent_id="investigation", suites=())]}

    text = describe_release_changes([change("soc-reasoning"), change("soc-report")], agents)

    assert "  investigation: no harness suite runs against this agent yet" in text
    assert "soc-report: " in text
    assert "  no agent manifest uses this alias" in text


def test_no_change_says_so() -> None:
    assert describe_release_changes([], {}) == (
        "No model release changed since the last recorded agent runs.\n"
    )


def releases(
    found: list[ModelReleaseChange], environ: Mapping[str, str]
) -> tuple[int, str, str, list[Mapping[str, ModelRelease]]]:
    asked: list[Mapping[str, ModelRelease]] = []

    async def double(
        sessions: SessionFactory, current: Mapping[str, ModelRelease]
    ) -> list[ModelReleaseChange]:
        asked.append(current)
        return found

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["--root", str(REPO_ROOT), "releases", "--registry", "config/models/registry.dev.yaml"],
        environ=environ,
        deps=Dependencies(release_changes=double),
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue(), asked


def test_the_command_exits_1_when_a_release_changed() -> None:
    code, out, _, asked = releases([change()], DATABASE)

    assert code == 1
    assert f"soc-fast: {change().describe()}" in out
    assert "triage: run the model gate with --suite adversarial-fn --suite trust-layers" in out
    assert asked == [load_model_releases(REGISTRY)]


def test_the_command_exits_0_when_nothing_changed() -> None:
    code, out, _, _ = releases([], DATABASE)

    assert (code, out) == (0, "No model release changed since the last recorded agent runs.\n")


def test_the_command_needs_the_database() -> None:
    code, out, err, asked = releases([change()], {})

    assert (code, out, asked) == (2, "", [])
    assert "AIS0C_DATABASE_URL is not set" in err


def test_the_command_needs_a_valid_registry() -> None:
    out, err = io.StringIO(), io.StringIO()

    code = main(
        ["--root", str(REPO_ROOT), "releases", "--registry", "config/models/missing.yaml"],
        environ=DATABASE,
        stdout=out,
        stderr=err,
    )

    assert code == 2
    assert "cannot read model registry" in err.getvalue()
