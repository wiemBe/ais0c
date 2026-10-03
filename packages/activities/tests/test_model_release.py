"""Model releases from the model registry, and what changed since the last recorded run (T-016
criteria 1 and 5).

The registry files are checked as they are in the repository: every alias has a release, and a
release value the registry does not know is null with the reason next to it.
"""

import copy
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from ais0c_activities import (
    ModelReleaseError,
    SessionFactory,
    compare_model_releases,
    load_model_releases,
    model_release_changes,
    parse_model_releases,
)
from ais0c_agents import load_model_registry
from ais0c_contracts import AgentTask, Budget, ModelRelease, TimeWindow
from ais0c_storage.repositories import start_agent_run

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRIES = {env: REPO_ROOT / f"config/models/registry.{env}.yaml" for env in ("dev", "prod")}
# The release fields a registry may leave unknown.
NULLABLE = (
    "tool_parser",
    "reasoning_parser",
    "artifact_hash",
    "quantization",
    "tokenizer",
    "engine_version",
)
T0 = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)

_TOP_LEVEL_KEY = re.compile(r"(?P<alias>[^\s#][^:]*):\s*(#.*)?")
_COMMENTED_FIELD = re.compile(r"\s+(?P<field>[a-z_]+):[^#]*#\s*\S.*")


def registry_data(env: str = "dev") -> dict[str, Any]:
    return yaml.safe_load(REGISTRIES[env].read_text(encoding="utf-8"))


def unexplained_nulls(text: str) -> list[str]:
    """`alias.field` of every null release field of a registry that has no comment on its line."""
    entries = yaml.safe_load(text)
    nulls = {
        (alias, field)
        for alias, entry in entries.items()
        for field in NULLABLE
        if field in entry and entry[field] is None
    }
    commented: set[tuple[str | None, str]] = set()
    alias: str | None = None
    for line in text.splitlines():
        if top := _TOP_LEVEL_KEY.fullmatch(line):
            alias = top["alias"]
        elif field := _COMMENTED_FIELD.fullmatch(line):
            commented.add((alias, field["field"]))
    return sorted(f"{alias}.{field}" for alias, field in nulls - commented)


def release(alias: str = "soc-fast", /, **changes: object) -> ModelRelease:
    return ModelRelease.model_validate(
        {
            "alias": alias,
            "target": "lab/model-a",
            "artifact": "example-org/Model-A",
            "artifact_hash": "3f1c2a9e8b7d6c5b4a39281706f5e4d3c2b1a090",
            "quantization": "fp8",
            "tokenizer": f"example-org/Model-A sha256:{'ab' * 32}",
            "engine_version": "0.11.2",
            "tool_parser": "example_parser",
            "max_context": 131072,
            "inference_params": {"forced_tool_choice": True, "parallel_tool_calls": False},
        }
        | changes
    )


# --- The registry files (criterion 1)


@pytest.mark.parametrize("env", sorted(REGISTRIES))
def test_every_alias_has_a_release_built_from_its_entry(env: str) -> None:
    entries = registry_data(env)
    releases = load_model_releases(REGISTRIES[env])

    # The aliases agents can run with (§8.4) are the ones with a release.
    assert list(releases) == list(entries) == list(load_model_registry(REGISTRIES[env]))
    for alias, built in releases.items():
        entry = entries[alias]
        assert (built.alias, built.target, built.artifact, built.tool_parser) == (
            alias,
            entry["target"],
            entry["artifact"],
            entry["tool_parser"],
        )
        assert built.max_context == entry["context_window"]
        assert (
            built.artifact_hash,
            built.quantization,
            built.tokenizer,
            built.engine_version,
        ) == (
            entry["artifact_hash"],
            entry["quantization"],
            entry["tokenizer"],
            entry["engine_version"],
        )
        settings = {
            "forced_tool_choice": entry["forced_tool_choice"],
            "parallel_tool_calls": entry["parallel_tool_calls"],
            "reasoning_parser": entry["reasoning_parser"],
        }
        assert built.inference_params == entry["inference_params"] | {
            name: value for name, value in settings.items() if value is not None
        }


@pytest.mark.parametrize("env", sorted(REGISTRIES))
def test_an_unknown_value_is_null_with_the_reason_next_to_it(env: str) -> None:
    assert unexplained_nulls(REGISTRIES[env].read_text(encoding="utf-8")) == []


def test_a_null_without_a_reason_is_found() -> None:
    text = (
        "# header: null\n"
        "soc-fast:\n"
        "  quantization: null\n"
        "  tokenizer: null  # not visible\n"
        "  turkish_quality: null\n"
        "soc-report:  # second\n"
        "  engine_version: ~\n"
        "  tokenizer: null  # not visible\n"
    )

    assert unexplained_nulls(text) == ["soc-fast.quantization", "soc-report.engine_version"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda e: e.pop("artifact"), "soc-fast.artifact"),
        (lambda e: e.pop("artifact_hash"), "soc-fast.artifact_hash"),
        (lambda e: e.pop("inference_params"), "soc-fast.inference_params"),
        (lambda e: e.update(artifact="  "), "soc-fast.artifact"),
        (lambda e: e.update(engine_version=""), "soc-fast.engine_version"),
        (lambda e: e.update(quantization=8), "soc-fast.quantization"),
        (lambda e: e.update(context_window="1M"), "soc-fast.context_window"),
        (lambda e: e.update(parallel_tool_calls="no"), "soc-fast.parallel_tool_calls"),
        (lambda e: e.update(inference_params={"stop": ["x"]}), "soc-fast.inference_params"),
        (
            lambda e: e.update(inference_params={"parallel_tool_calls": True}),
            "soc-fast: inference_params must not set parallel_tool_calls",
        ),
    ],
    ids=[
        "no-artifact",
        "null-left-out",
        "no-inference-params",
        "blank-artifact",
        "empty-string-for-null",
        "number-for-text",
        "window-not-int",
        "loose-bool",
        "list-parameter",
        "request-setting-repeated",
    ],
)
def test_an_entry_without_a_valid_release_is_refused(
    change: Callable[[dict[str, Any]], object], message: str
) -> None:
    data = copy.deepcopy(registry_data())
    change(data["soc-fast"])

    with pytest.raises(ModelReleaseError, match=re.escape(message)):
        parse_model_releases(data)


def test_an_unknown_alias_or_an_unreadable_registry_is_refused(tmp_path: Path) -> None:
    data = registry_data()
    data["soc-unknown"] = data["soc-fast"]
    with pytest.raises(ModelReleaseError, match="soc-unknown"):
        parse_model_releases(data)

    with pytest.raises(ModelReleaseError, match="cannot read model registry"):
        load_model_releases(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("soc-fast: [unclosed\n", encoding="utf-8")
    with pytest.raises(ModelReleaseError, match="cannot read model registry"):
        load_model_releases(broken)
    listed = tmp_path / "list.yaml"
    listed.write_text("- soc-fast\n", encoding="utf-8")
    with pytest.raises(ModelReleaseError, match="invalid model registry"):
        load_model_releases(listed)


def test_inference_params_add_the_request_settings_to_the_sampling_parameters() -> None:
    data = registry_data("prod")
    entry = data["soc-fast"]
    entry["inference_params"] = {"temperature": 0.2, "top_p": 0.95, "seed": 7}
    # Not sent when null (D-39), so not part of the release either.
    entry["parallel_tool_calls"] = None

    built = parse_model_releases(data)["soc-fast"]

    assert built.inference_params == {
        "temperature": 0.2,
        "top_p": 0.95,
        "seed": 7,
        "forced_tool_choice": entry["forced_tool_choice"],
        "reasoning_parser": entry["reasoning_parser"],
    }


# --- Changes against the recorded releases (criterion 5)


def test_a_changed_release_is_reported_with_the_fields_that_differ() -> None:
    recorded = {"soc-fast": release(), "soc-verifier": release("soc-verifier")}
    current = {
        "soc-fast": release(engine_version="0.12.0", quantization="bf16"),
        "soc-verifier": release("soc-verifier"),
    }

    [change] = compare_model_releases(recorded, current)

    assert (change.alias, change.recorded, change.current) == (
        "soc-fast",
        recorded["soc-fast"],
        current["soc-fast"],
    )
    assert change.fields == ("quantization", "engine_version")
    assert (
        change.describe() == "quantization: 'fp8' -> 'bf16'; engine_version: '0.11.2' -> '0.12.0'"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target", "lab/model-b"),
        ("artifact", "example-org/Model-A-Instruct"),
        ("artifact_hash", "0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e"),
        ("quantization", None),
        ("tokenizer", f"example-org/Model-A sha256:{'cd' * 32}"),
        ("engine_version", "0.11.3"),
        ("tool_parser", "other_parser"),
        ("max_context", 65536),
        ("inference_params", {"forced_tool_choice": False, "parallel_tool_calls": False}),
        ("inference_params", {"forced_tool_choice": True}),
    ],
)
def test_a_change_in_any_field_is_a_new_release(field: str, value: object) -> None:
    [change] = compare_model_releases(
        {"soc-fast": release()}, {"soc-fast": release(**{field: value})}
    )

    assert change.fields == (field,)


def test_an_equal_release_or_an_alias_on_one_side_only_is_no_change() -> None:
    assert compare_model_releases({"soc-fast": release()}, {"soc-fast": release()}) == []
    # Nothing recorded yet for soc-fast; soc-report is no longer configured.
    assert (
        compare_model_releases({"soc-report": release("soc-report")}, {"soc-fast": release()}) == []
    )


async def record_run(
    sessions: SessionFactory, run_id: str, model_release: ModelRelease | None, started_at: datetime
) -> None:
    alias = "none" if model_release is None else model_release.alias
    task = AgentTask(
        task_id=run_id,
        parent_run_id="case-run-1",
        case_id="case-7",
        agent_id="triage",
        agent_version="1.0.0",
        objective="Triage QRadar offense 7 (evaluation 1).",
        context_refs=[],
        time_window=TimeWindow(start=started_at - timedelta(hours=1), end=started_at),
        budget=Budget(tokens=60000, tool_calls=12, seconds=180),
    )
    async with sessions.begin() as session:
        await start_agent_run(
            session,
            run_id=run_id,
            task=task,
            prompt_version="triage/v1",
            model_alias=alias,
            model_target="none" if model_release is None else model_release.target,
            toolset_profile="qradar-triage-read",
            started_at=started_at,
            model_release=model_release,
        )


@pytest.mark.anyio
async def test_changes_are_found_against_the_last_run_of_each_alias(
    sessions: SessionFactory,
) -> None:
    await record_run(sessions, "run-1", release(engine_version="0.10.1"), T0)
    await record_run(sessions, "run-2", release(), T0 + timedelta(hours=1))
    await record_run(sessions, "run-3", release("soc-verifier", engine_version="0.10.1"), T0)
    await record_run(sessions, "run-4", None, T0 + timedelta(hours=2))
    current = {
        "soc-fast": release(),
        "soc-verifier": release("soc-verifier"),
        "soc-report": release("soc-report"),
    }

    changes = await model_release_changes(sessions, current)

    # soc-fast's last run used the current release; soc-report has no run yet.
    assert [(change.alias, change.fields) for change in changes] == [
        ("soc-verifier", ("engine_version",))
    ]
    assert changes[0].recorded == release("soc-verifier", engine_version="0.10.1")


@pytest.mark.anyio
async def test_a_release_read_back_from_the_database_equals_the_registrys(
    sessions: SessionFactory,
) -> None:
    """A release survives the jsonb round trip unchanged, so an unchanged registry reports no
    change for any alias. Prod's runs come last, so they are the last of each alias."""
    for hours, (env, path) in enumerate(REGISTRIES.items()):
        releases = load_model_releases(path)
        for built in releases.values():
            await record_run(sessions, f"{env}-{built.alias}", built, T0 + timedelta(hours=hours))

        assert await model_release_changes(sessions, releases) == []
