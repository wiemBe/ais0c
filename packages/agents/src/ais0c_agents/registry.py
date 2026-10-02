"""Model capability registry (architecture §8.4): config/models/registry.<env>.yaml.

One entry per model alias. Agents read only what starting a run needs: the capabilities, which
the agent manifest is checked against, and the request settings. The other fields (target,
prod_equivalent, parsers, Turkish quality) are for LiteLLM, vLLM and people, and are ignored here.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError
from pydantic_ai.settings import ModelSettings

from ais0c_agents._yaml import load_yaml
from ais0c_agents.llm import ModelAlias

Capability = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class ModelRegistryError(ValueError):
    """The model registry file is missing, not valid YAML or not in the §8.4 format."""


class ModelRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    capabilities: frozenset[Capability]
    parallel_tool_calls: bool
    context_window: Annotated[int, Field(gt=0)]

    def model_settings(self) -> ModelSettings:
        """Request settings every call to this model must use."""
        return ModelSettings(parallel_tool_calls=self.parallel_tool_calls)


ModelRegistry = Mapping[ModelAlias, ModelRegistryEntry]

_REGISTRY = TypeAdapter(dict[ModelAlias, ModelRegistryEntry])


def parse_model_registry(data: object) -> ModelRegistry:
    """Validate registry data. An unknown alias is an error, not ignored."""
    try:
        return _REGISTRY.validate_python(data)
    except ValidationError as error:
        raise ModelRegistryError(f"invalid model registry: {error}") from error


def load_model_registry(path: Path) -> ModelRegistry:
    try:
        data = load_yaml(path)
    except (OSError, yaml.YAMLError) as error:
        raise ModelRegistryError(f"cannot read model registry {path}: {error}") from error
    return parse_model_registry(data)
