"""The agent under test, loaded from the files the case worker loads (T-030 criterion 3).

The worker (`ais0c_activities.runtime.load_case_runtime`) reads the agent's manifest
`config/agents/<agent>.yaml` against the model registry, loads the prompt and shared rules the
manifest names, builds the model from the registry entry of the manifest's alias and asks the
gateway for its profile's tools. The harness does the same, except that it reads the profile
from the gateway's own registry (`config/connectors/`, `config/policies/`) instead of over HTTP:
`Profile.tool_list()` is exactly what the gateway serves at `GET /v1/tools`, descriptions and
schemas included.

The model is LiteLLM's alias, as in the worker. `build_model` has no default address; the
runner uses the e2e tests' `http://127.0.0.1:4000` when `LITELLM_BASE_URL` is not set.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml
from pydantic import ValidationError
from pydantic_ai.models import Model

from ais0c_activities.model_release import ModelReleaseError, load_model_releases
from ais0c_agents import (
    LITELLM_BASE_URL_ENV,
    AgentManifest,
    ManifestError,
    ModelRegistryEntry,
    ModelRegistryError,
    PromptError,
    PromptTemplate,
    ToolsetProfile,
    build_model,
    load_agent_prompt,
    load_manifest,
    load_model_registry,
)
from ais0c_contracts import ModelRelease
from ais0c_mcp_gateway.registry import Profile, RegistryError, load_registry

DEFAULT_REGISTRY: Final = "config/models/registry.dev.yaml"
DEFAULT_LITELLM_BASE_URL: Final = "http://127.0.0.1:4000"
# The connectors the gateway loads by default (AIS0C_GATEWAY_CONNECTORS).
GATEWAY_CONNECTORS: Final = ("qradar",)
GATEWAY_CONFIG_DIR: Final = "config"
AGENT_MANIFESTS: Final = "config/agents"


class ConfigError(ValueError):
    """A file the agent under test is built from is missing or invalid."""


@dataclass(frozen=True)
class AgentConfig:
    """What one agent is built from, as the worker builds it."""

    manifest: AgentManifest
    prompt: PromptTemplate
    registry_entry: ModelRegistryEntry
    """The model registry's entry of the manifest's alias: request settings and tool choice."""
    model_release: ModelRelease
    """The release of the manifest's alias (T-24), which every run records."""
    gateway_profile: Profile
    """The gateway's profile of the manifest: its tools, schemas and intent rules."""
    profile: ToolsetProfile
    """The same profile as the agent receives it from the gateway."""

    @property
    def toolset_sha256(self) -> str:
        """sha256 of the tool list the agent sees, descriptions and schemas included."""
        return sha256_json(self.profile.model_dump(mode="json"))


def load_agent_config(root: Path, manifest_path: str, registry_path: Path) -> AgentConfig:
    """Load the agent of `manifest_path` (under `root`) against the model registry file.

    Raises ConfigError when a file is missing or invalid, the manifest names no toolset
    profile, or the gateway has no such profile.
    """
    try:
        registry = load_model_registry(registry_path)
        manifest = load_manifest(root / manifest_path, registry)
        prompt = load_agent_prompt(root, manifest)
        releases = load_model_releases(registry_path)
        gateway = load_registry(root / GATEWAY_CONFIG_DIR, GATEWAY_CONNECTORS)
    except (
        ManifestError,
        ModelRegistryError,
        ModelReleaseError,
        PromptError,
        RegistryError,
        OSError,
    ) as error:
        raise ConfigError(f"{type(error).__name__}: {error}") from error
    name = manifest.toolset_profile
    if name is None:
        raise ConfigError(f"the {manifest.id} manifest names no toolset profile")
    gateway_profile = gateway.profiles.get(name)
    if gateway_profile is None:
        raise ConfigError(f"the gateway has no profile {name!r}")
    try:
        profile = ToolsetProfile.model_validate(gateway_profile.tool_list())
    except ValidationError as error:
        raise ConfigError(f"the gateway's {name} is not an agent profile: {error}") from error
    return AgentConfig(
        manifest=manifest,
        prompt=prompt,
        registry_entry=registry[manifest.model_alias],
        # load_manifest found the alias in the registry, and every entry has a release.
        model_release=releases[manifest.model_alias],
        gateway_profile=gateway_profile,
        profile=profile,
    )


def litellm_model(config: AgentConfig, environ: Mapping[str, str]) -> Model:
    """The agent's model as the worker builds it: its alias through LiteLLM, with the registry
    entry's request settings and tool choice."""
    env = dict(environ)
    if not env.get(LITELLM_BASE_URL_ENV, "").strip():
        env[LITELLM_BASE_URL_ENV] = DEFAULT_LITELLM_BASE_URL
    entry = config.registry_entry
    return build_model(
        config.manifest.model_alias,
        settings=entry.model_settings(),
        environ=env,
        forced_tool_choice=entry.forced_tool_choice,
    )


def agent_aliases(root: Path) -> dict[str, str]:
    """Agent ID -> model alias, from every manifest under config/agents/."""
    aliases: dict[str, str] = {}
    for path in sorted((root / AGENT_MANIFESTS).glob("*.yaml")):
        try:
            data: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise ConfigError(f"cannot read agent manifest {path}: {error}") from error
        if not isinstance(data, dict):
            raise ConfigError(f"agent manifest {path} is not a mapping")
        agent_id, alias = data.get("id"), data.get("model_alias")
        if not isinstance(agent_id, str) or not isinstance(alias, str):
            raise ConfigError(f"agent manifest {path} lacks id or model_alias")
        aliases[agent_id] = alias
    return aliases


def sha256_json(value: object) -> str:
    """sha256 of a JSON value in canonical form (sorted keys, no spaces)."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
