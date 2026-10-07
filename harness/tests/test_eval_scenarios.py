"""Suite and scenario files (T-030 criterion 1): the format, the versions and every reason a
file is rejected."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from ais0c_harness.eval import (
    Suite,
    SuiteError,
    TriageScenario,
    load_scenario,
    load_suite,
    load_suites,
)

from .eval_helpers import REPO_ROOT, SUITES, scenario, scenario_data, write_suite


def edit(path: Path, **changes: object) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def copied(tmp_path: Path, source: str = "tl-01-catalog-note-fp") -> Path:
    """A security suite `copies` holding one copy of `source`, as cp-01-copy.yaml."""
    return write_suite(tmp_path, suite_id="copies", kind="security", prefix="cp-", sources=[source])


# --- the format ---------------------------------------------------------------------------------


def test_each_suite_has_a_suite_yaml() -> None:
    suites = {suite.id: suite for suite in load_suites(REPO_ROOT)}

    assert set(suites) == {
        "trust-layers",
        "adversarial-fn",
        "investigation-gold",
        "verification-gold",
        "orchestrator-gold",
        "reporting-gold",
        "turkish-quality",
    }
    for suite_id, kind, agent, prefix in (
        ("trust-layers", "security", "triage", "tl-"),
        ("adversarial-fn", "security", "triage", "afn-"),
        ("investigation-gold", "quality", "investigation", "inv-"),
        ("verification-gold", "quality", "verification", "ver-"),
    ):
        definition = suites[suite_id].definition
        assert definition.id == suites[suite_id].path.name == suite_id
        assert (definition.kind, definition.agent, definition.scenario_prefix) == (
            kind,
            agent,
            prefix,
        )
        assert definition.title


def test_the_trust_layers_files_load_unchanged() -> None:
    suite = load_suite(SUITES / "trust-layers", root=REPO_ROOT)

    assert [item.id for item in suite.scenarios] == [
        "tl-01-catalog-note-fp",
        "tl-02-runbook-instruction",
        "tl-03-log-imitates-org-context",
    ]
    for item in suite.scenarios:
        assert isinstance(item.scenario, TriageScenario)
        assert (item.scenario.suite, item.scenario.agent) == ("trust-layers", "triage")


def test_evaluated_at_defaults_to_five_minutes_after_the_last_update(tmp_path: Path) -> None:
    played = scenario("tl-01-catalog-note-fp")
    assert played.evaluated_at == played.input.offense.last_updated_time + timedelta(minutes=5)

    path = copied(tmp_path) / "cp-01-copy.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["input"]["evaluated_at"] = "2026-10-02T07:00:00+03:00"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    loaded = load_scenario(path, root=REPO_ROOT).scenario

    assert isinstance(loaded, TriageScenario)
    assert loaded.evaluated_at == datetime(2026, 10, 2, 4, 0, tzinfo=UTC)


def test_required_tools_and_max_tool_calls_are_optional(tmp_path: Path) -> None:
    path = copied(tmp_path) / "cp-01-copy.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["expect"]["required_tools"] = ["get_offense", "get_rule"]
    data["expect"]["max_tool_calls"] = 4
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    loaded = load_scenario(path, root=REPO_ROOT).scenario
    expect = loaded.expectation()

    assert expect.required_tools == {"get_offense", "get_rule"}
    assert expect.max_tool_calls == 4
    default = scenario("tl-01-catalog-note-fp").expect
    assert (default.required_tools, default.max_tool_calls) == (frozenset(), None)


# --- versions -----------------------------------------------------------------------------------


def test_a_scenario_version_is_the_sha256_of_its_file() -> None:
    for item in load_suite(SUITES / "adversarial-fn", root=REPO_ROOT).scenarios:
        assert item.version == hashlib.sha256(item.path.read_bytes()).hexdigest()


def test_a_suite_version_follows_its_files(tmp_path: Path) -> None:
    directory = write_suite(
        tmp_path,
        suite_id="copies",
        kind="security",
        prefix="cp-",
        sources=["tl-01-catalog-note-fp", "tl-02-runbook-instruction"],
    )

    def version() -> str:
        return load_suite(directory, root=REPO_ROOT).version

    first = version()
    assert version() == first
    edit(directory / "cp-02-copy.yaml", title="Another title")
    second = version()
    assert second != first
    edit(directory / "suite.yaml", title="Renamed")
    assert version() not in (first, second)


# --- every reason to reject a file --------------------------------------------------------------


def rejected(path: Path) -> str:
    with pytest.raises(SuiteError) as error:
        load_suite(path, root=REPO_ROOT)
    return str(error.value)


def test_an_unknown_scenario_field_is_rejected(tmp_path: Path) -> None:
    directory = copied(tmp_path)
    edit(directory / "cp-01-copy.yaml", severity="high")

    assert "severity" in rejected(directory)


def test_an_unknown_expectation_field_is_rejected(tmp_path: Path) -> None:
    directory = copied(tmp_path)
    path = directory / "cp-01-copy.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["expect"]["forbidden_tools"] = ["add_offense_note"]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    assert "forbidden_tools" in rejected(directory)


def test_an_unknown_suite_field_is_rejected(tmp_path: Path) -> None:
    directory = copied(tmp_path)
    edit(directory / "suite.yaml", gate="soft")

    assert "gate" in rejected(directory)


def test_a_file_named_other_than_its_id_is_rejected(tmp_path: Path) -> None:
    directory = copied(tmp_path)
    (directory / "cp-01-copy.yaml").rename(directory / "cp-01-renamed.yaml")

    assert "is not the file's name" in rejected(directory)


def test_an_id_without_the_suite_prefix_is_rejected(tmp_path: Path) -> None:
    directory = copied(tmp_path)
    (directory / "cp-01-copy.yaml").unlink()
    data = scenario_data("tl-01-catalog-note-fp")
    data["suite"] = "copies"
    (directory / "tl-01-catalog-note-fp.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    assert "lacks the prefix 'cp-'" in rejected(directory)


def test_a_suite_field_other_than_the_directory_is_rejected(tmp_path: Path) -> None:
    directory = copied(tmp_path)
    edit(directory / "cp-01-copy.yaml", suite="trust-layers")

    assert "is not the directory 'copies'" in rejected(directory)


def test_an_agent_other_than_the_suite_agent_is_rejected(tmp_path: Path) -> None:
    directory = copied(tmp_path)
    edit(directory / "cp-01-copy.yaml", agent="investigation")

    assert "is not the suite's agent 'triage'" in rejected(directory)


@pytest.mark.parametrize("tool_id", ["add_offense_note", "create_ariel_search", "no_such_tool"])
def test_a_result_for_a_tool_outside_the_profile_is_rejected(tmp_path: Path, tool_id: str) -> None:
    directory = copied(tmp_path)
    path = directory / "cp-01-copy.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["input"]["tool_results"][tool_id] = data["input"]["tool_results"]["get_offense"]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    assert f"results for {tool_id}, not in the agent's profile" in rejected(directory)


def test_a_required_tool_outside_the_profile_is_rejected(tmp_path: Path) -> None:
    directory = copied(tmp_path)
    path = directory / "cp-01-copy.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["expect"]["required_tools"] = ["create_ariel_search"]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    assert "requires create_ariel_search" in rejected(directory)


def test_a_suite_id_other_than_its_directory_is_rejected(tmp_path: Path) -> None:
    directory = copied(tmp_path)
    edit(directory / "suite.yaml", id="other")

    assert "is not the directory's name" in rejected(directory)


@pytest.mark.parametrize("agent", ["hunter", "endpoint"])
def test_a_suite_for_another_agent_is_not_supported_yet(tmp_path: Path, agent: str) -> None:
    directory = copied(tmp_path)
    edit(directory / "suite.yaml", agent=agent)

    message = rejected(directory)

    assert f"agent {agent!r} is not supported yet" in message
    assert "triage" in message


def test_an_evaluation_before_the_last_update_is_rejected(tmp_path: Path) -> None:
    directory = copied(tmp_path)
    path = directory / "cp-01-copy.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["input"]["evaluated_at"] = "2026-10-02T03:13:00Z"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    assert "evaluated_at is before the offense's last update" in rejected(directory)


def test_an_evidence_id_the_wrapper_refuses_is_rejected(tmp_path: Path) -> None:
    directory = copied(tmp_path)
    path = directory / "cp-01-copy.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["input"]["tool_results"]["get_offense"][0]["evidence_id"] = 'ev_"quoted"'
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    assert "unusable evidence_id" in rejected(directory)


def test_a_file_that_is_not_yaml_is_rejected(tmp_path: Path) -> None:
    directory = copied(tmp_path)
    (directory / "cp-01-copy.yaml").write_text("id: [unclosed\n", encoding="utf-8")

    assert "cannot read" in rejected(directory)


def test_an_unknown_suite_id_is_rejected() -> None:
    with pytest.raises(SuiteError, match="no suite prompt-injection"):
        load_suites(REPO_ROOT, ["trust-layers", "prompt-injection"])


def test_suites_load_in_the_order_asked() -> None:
    suites: list[Suite] = load_suites(REPO_ROOT, ["adversarial-fn", "trust-layers"])

    assert [suite.id for suite in suites] == ["adversarial-fn", "trust-layers"]
