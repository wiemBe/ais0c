"""The model release record (T-24, architecture §19): the real identity of the model behind each
model alias.

The model registry (config/models/registry.<env>.yaml) carries the `ModelRelease` fields of
every alias (docs/impl/contracts.md). `load_model_releases` builds the releases from it: alias,
target, artifact, artifact_hash, quantization, tokenizer, engine_version and tool_parser as
written, max_context from the entry's context_window, and inference_params from the entry's
sampling parameters together with the request settings the platform sends (parallel_tool_calls,
forced_tool_choice) and the server's reasoning parser.

Every agent run records the release of its alias in `agent_runs.model_release`. A release that
differs from the last one recorded for its alias is a new model release, and the model gate
runs again for it (docs/agent-harness.md §5, B2): the worker warns at start-up and the harness
(T-030) starts the gate from `model_release_changes`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

from ais0c_activities.db import SessionFactory
from ais0c_agents import ModelAlias
from ais0c_contracts import ModelRelease
from ais0c_storage.repositories import latest_model_releases

type InferenceValue = str | int | float | bool

# Not empty and not only spaces.
_Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
# Registry fields that go into a release's inference_params under their own name; an entry's
# `inference_params` must not set them again.
REQUEST_SETTINGS: Final = (
    "forced_tool_choice",
    "parallel_tool_calls",
    "provider",
    "reasoning_parser",
)


class ModelReleaseError(ValueError):
    """The model registry is missing, not valid YAML or lacks a model release field."""


class _ReleaseEntry(BaseModel):
    """The fields of a registry entry that make up a release; the others are ignored here."""

    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    target: _Text
    context_window: Annotated[int, Field(gt=0)]
    tool_parser: _Text | None
    reasoning_parser: _Text | None
    parallel_tool_calls: bool | None
    # As the agents read it (ais0c_agents.registry): absent means true.
    forced_tool_choice: bool = True
    # The pinned hosted provider (dev); absent in prod.
    provider: _Text | None = None
    artifact: _Text
    # Required, but null when the value is not known (the registry says why next to it).
    artifact_hash: _Text | None
    quantization: _Text | None
    tokenizer: _Text | None
    engine_version: _Text | None
    inference_params: dict[str, InferenceValue]


_REGISTRY: Final = TypeAdapter(dict[ModelAlias, _ReleaseEntry])


def parse_model_releases(data: object) -> dict[str, ModelRelease]:
    """The release of every alias in registry data. An unknown alias, or an entry without one of
    the release fields, is an error: an agent run must not start without its release."""
    try:
        entries = _REGISTRY.validate_python(data)
    except ValidationError as error:
        raise ModelReleaseError(f"invalid model registry: {error}") from error
    return {alias: _release(alias, entry) for alias, entry in entries.items()}


def load_model_releases(path: Path) -> dict[str, ModelRelease]:
    """The release of every alias in the model registry at `path`."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ModelReleaseError(f"cannot read model registry {path}: {error}") from error
    return parse_model_releases(data)


def _release(alias: str, entry: _ReleaseEntry) -> ModelRelease:
    if repeated := sorted(entry.inference_params.keys() & set(REQUEST_SETTINGS)):
        raise ModelReleaseError(
            f"{alias}: inference_params must not set {', '.join(repeated)}; "
            "the release takes these from the entry's own fields"
        )
    settings: dict[str, InferenceValue] = {"forced_tool_choice": entry.forced_tool_choice}
    if entry.parallel_tool_calls is not None:
        settings["parallel_tool_calls"] = entry.parallel_tool_calls
    if entry.provider is not None:
        settings["provider"] = entry.provider
    if entry.reasoning_parser is not None:
        settings["reasoning_parser"] = entry.reasoning_parser
    return ModelRelease(
        alias=alias,
        target=entry.target,
        artifact=entry.artifact,
        artifact_hash=entry.artifact_hash,
        quantization=entry.quantization,
        tokenizer=entry.tokenizer,
        engine_version=entry.engine_version,
        tool_parser=entry.tool_parser,
        max_context=entry.context_window,
        inference_params={**entry.inference_params, **settings},
    )


@dataclass(frozen=True)
class ModelReleaseChange:
    """An alias whose release now differs from the one its last run recorded."""

    alias: str
    recorded: ModelRelease
    current: ModelRelease

    @property
    def fields(self) -> tuple[str, ...]:
        """The fields that differ, in the order of the contract."""
        return tuple(
            name
            for name in ModelRelease.model_fields
            if getattr(self.recorded, name) != getattr(self.current, name)
        )

    def describe(self) -> str:
        """The fields that differ with both values: "engine_version: '0.11.0' -> '0.11.2'"."""
        return "; ".join(
            f"{name}: {getattr(self.recorded, name)!r} -> {getattr(self.current, name)!r}"
            for name in self.fields
        )


def compare_model_releases(
    recorded: Mapping[str, ModelRelease], current: Mapping[str, ModelRelease]
) -> list[ModelReleaseChange]:
    """The aliases whose `current` release differs from the `recorded` one, in the order of
    `current`.

    An alias without a recorded release is not a change, since no run has used it yet; nor is
    a recorded alias that `current` no longer has.
    """
    return [
        ModelReleaseChange(alias=alias, recorded=recorded[alias], current=release)
        for alias, release in current.items()
        if alias in recorded and recorded[alias] != release
    ]


async def model_release_changes(
    sessions: SessionFactory, current: Mapping[str, ModelRelease]
) -> list[ModelReleaseChange]:
    """The aliases whose release in `current`, normally the model registry's, differs from the
    one their last agent run recorded in `agent_runs`.

    Each is a new model release, for which the model gate must run again
    (docs/agent-harness.md §5, B2). The worker warns about them at start-up; the harness (T-030)
    starts the gate from this function.
    """
    async with sessions() as session:
        recorded = await latest_model_releases(session)
    return compare_model_releases(recorded, current)
