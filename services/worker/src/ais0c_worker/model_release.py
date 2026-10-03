"""Compare the prod model registry with the vLLM servers (T-016):

    uv run python -m ais0c_worker.model_release verify

Run at deployment, never during agent runs: agents reach models only through LiteLLM. For each
alias of the registry, the tool finds where LiteLLM sends it, from `litellm_params` in the
LiteLLM configuration: `model`, `api_base` and `api_key`, a value written `os.environ/<NAME>`
being read from the environment as LiteLLM reads it. It asks each of those vLLM servers for
`/v1/models` and `/version` and compares:

| Registry | Compared with |
|---|---|
| `target` | LiteLLM's `model`; the name after its provider prefix must be a model `id` in `/v1/models` |
| `artifact` | that model's `root`: what `vllm serve` loaded |
| `max_context` (`context_window`) | that model's `max_model_len` |
| `engine_version` | `version` in `/version` |

A release without `artifact_hash` is reported too, since contracts.md requires it in prod.
Quantization, tokenizer, the parsers and inference_params are not in vLLM's API; they are read
on the server by hand (config/models/registry.prod.yaml).

| Option | Default |
|---|---|
| `--registry` | `config/models/registry.prod.yaml` |
| `--litellm-config` | `config/litellm/litellm.prod.yaml` |
| `--timeout` | 10 seconds per request |

Relative paths are taken from `AIS0C_WORKER_ROOT` (default: the current directory).

Exit status: 0 when the registry matches the servers; 1 when something differs or a server
cannot be read, each on a line of its own; 2 when the registry, the LiteLLM configuration or an
environment variable it names is missing or invalid.
"""

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import httpx2
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from ais0c_activities import ModelRelease, ModelReleaseError, load_model_releases

ROOT_ENV: Final = "AIS0C_WORKER_ROOT"
DEFAULT_REGISTRY: Final = Path("config/models/registry.prod.yaml")
DEFAULT_LITELLM_CONFIG: Final = Path("config/litellm/litellm.prod.yaml")
DEFAULT_TIMEOUT_SECONDS: Final = 10.0
# How the LiteLLM configuration names an environment variable.
ENV_PREFIX: Final = "os.environ/"
# Release fields vLLM's API does not report.
CHECKED_BY_HAND: Final = ("quantization", "tokenizer", "tool_parser", "inference_params")

EXIT_MATCH: Final = 0
EXIT_DIFFERENT: Final = 1
EXIT_CONFIG_ERROR: Final = 2


class VerifyConfigError(ValueError):
    """The LiteLLM configuration or an environment variable it names is missing or invalid."""


class ServerError(RuntimeError):
    """A vLLM server cannot be read."""


@dataclass(frozen=True)
class Upstream:
    """One server LiteLLM sends an alias to."""

    model: str
    api_base: str
    api_key: str | None = field(default=None, repr=False)

    @property
    def server_url(self) -> str:
        """The vLLM server's address, without the /v1 path of `api_base`."""
        return self.api_base.rstrip("/").removesuffix("/v1")

    @property
    def served_name(self) -> str:
        """The model name LiteLLM sends to the server: `model` without its provider prefix."""
        return self.model.partition("/")[2] or self.model


class ServedModel(BaseModel):
    """A model card of vLLM's /v1/models; its other fields are ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    root: str | None = None
    max_model_len: int | None = None


class _ModelList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[ServedModel]


class _Version(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: str


@dataclass(frozen=True)
class ServerInfo:
    """What a vLLM server reports: its models by `id` and its version."""

    models: Mapping[str, ServedModel]
    version: str


def litellm_models(path: Path) -> dict[str, list[Mapping[str, object]]]:
    """The `litellm_params` of every deployment of each alias in the LiteLLM configuration."""
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise VerifyConfigError(f"cannot read the LiteLLM configuration {path}: {error}") from error
    entries = config.get("model_list") if isinstance(config, dict) else None
    if not isinstance(entries, list):
        raise VerifyConfigError(f"{path} has no model_list")
    models: dict[str, list[Mapping[str, object]]] = {}
    for entry in entries:
        alias = entry.get("model_name") if isinstance(entry, dict) else None
        params = entry.get("litellm_params") if isinstance(entry, dict) else None
        if not isinstance(alias, str) or not isinstance(params, dict):
            raise VerifyConfigError(
                f"{path}: a model_list entry lacks model_name or litellm_params"
            )
        models.setdefault(alias, []).append(params)
    return models


def upstream(alias: str, params: Mapping[str, object], environ: Mapping[str, str]) -> Upstream:
    """Where one deployment of `alias` in the LiteLLM configuration goes."""
    model = params.get("model")
    if not isinstance(model, str) or not model.strip():
        raise VerifyConfigError(f"the LiteLLM configuration gives {alias} no model")
    api_base = _setting(params, "api_base", alias, environ)
    if api_base is None:
        raise VerifyConfigError(f"the LiteLLM configuration gives {alias} no api_base")
    if not api_base.startswith(("http://", "https://")):
        raise VerifyConfigError(f"the api_base of {alias} must be an http(s) URL")
    return Upstream(
        model=model, api_base=api_base, api_key=_setting(params, "api_key", alias, environ)
    )


def _setting(
    params: Mapping[str, object], name: str, alias: str, environ: Mapping[str, str]
) -> str | None:
    """A LiteLLM setting as LiteLLM reads it; None when it is absent or empty."""
    value = params.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise VerifyConfigError(f"the {name} of {alias} is not a string")
    if not value.startswith(ENV_PREFIX):
        return value.strip() or None
    variable = value.removeprefix(ENV_PREFIX)
    resolved = environ.get(variable, "").strip()
    if not resolved and name == "api_base":
        raise VerifyConfigError(f"{variable} is not set; it holds the api_base of {alias}")
    return resolved or None


def read_server(client: httpx2.Client, target: Upstream) -> ServerInfo:
    """Ask the vLLM server of `target` for its models and its version."""
    headers = {"Authorization": f"Bearer {target.api_key}"} if target.api_key else {}
    models = _get(client, f"{target.server_url}/v1/models", headers, _ModelList)
    version = _get(client, f"{target.server_url}/version", headers, _Version)
    return ServerInfo(models={model.id: model for model in models.data}, version=version.version)


def _get[M: BaseModel](
    client: httpx2.Client, url: str, headers: Mapping[str, str], answer: type[M]
) -> M:
    try:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        return answer.model_validate_json(response.content)
    except httpx2.HTTPError as error:
        raise ServerError(f"cannot read {url}: {error}") from error
    except ValidationError:
        raise ServerError(f"{url} does not answer as vLLM does") from None


def server_differences(release: ModelRelease, target: Upstream, server: ServerInfo) -> list[str]:
    """How the release differs from LiteLLM's deployment `target` and its vLLM server."""
    found: list[str] = []
    if target.model != release.target:
        found.append(f"target: registry {release.target!r}, LiteLLM {target.model!r}")
    model = server.models.get(target.served_name)
    if model is None:
        found.append(
            f"the server does not serve {target.served_name!r}; it serves {sorted(server.models)}"
        )
    else:
        if model.root != release.artifact:
            found.append(f"artifact: registry {release.artifact!r}, server {model.root!r}")
        if model.max_model_len != release.max_context:
            found.append(
                f"max_context: registry {release.max_context!r}, server {model.max_model_len!r}"
            )
    if server.version != release.engine_version:
        found.append(
            f"engine_version: registry {release.engine_version!r}, server {server.version!r}"
        )
    return found


def verify(
    registry: Path,
    litellm_config: Path,
    environ: Mapping[str, str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    transport: httpx2.BaseTransport | None = None,
) -> list[str]:
    """The differences between the registry and the servers, one line each; empty when they match.

    Raises `ModelReleaseError` or `VerifyConfigError` when an input is missing or invalid.
    """
    releases = load_model_releases(registry)
    deployments = litellm_models(litellm_config)
    targets = {
        alias: [upstream(alias, params, environ) for params in deployments.get(alias, [])]
        for alias in releases
    }
    servers: dict[tuple[str, str | None], ServerInfo | ServerError] = {}
    problems: list[str] = []
    with httpx2.Client(timeout=timeout, transport=transport) as client:
        for alias, release in releases.items():
            if release.artifact_hash is None:
                problems.append(
                    f"{alias}: artifact_hash is not recorded; contracts.md requires it in prod"
                )
            if not targets[alias]:
                problems.append(f"{alias}: {litellm_config} has no deployment for it")
            for target in targets[alias]:
                key = (target.server_url, target.api_key)
                if key not in servers:
                    try:
                        servers[key] = read_server(client, target)
                    except ServerError as error:
                        servers[key] = error
                server = servers[key]
                found = (
                    [str(server)]
                    if isinstance(server, ServerError)
                    else server_differences(release, target, server)
                )
                problems.extend(f"{alias} at {target.server_url}: {line}" for line in found)
    return problems


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    """Run the command line; returns the exit status."""
    parser = argparse.ArgumentParser(
        prog="python -m ais0c_worker.model_release",
        description="Model release records of the model registry (T-24).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser(
        "verify", help="compare the prod model registry with the vLLM servers LiteLLM uses"
    )
    check.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    check.add_argument("--litellm-config", type=Path, default=DEFAULT_LITELLM_CONFIG)
    check.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    env = os.environ if environ is None else environ
    root = Path(env.get(ROOT_ENV, ".") or ".")
    try:
        problems = verify(
            root / args.registry, root / args.litellm_config, env, timeout=args.timeout
        )
    except (ModelReleaseError, VerifyConfigError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    for problem in problems:
        print(problem)
    print(f"Not in vLLM's API, read on the server by hand: {', '.join(CHECKED_BY_HAND)}.")
    if problems:
        print(f"The registry differs from the servers in {len(problems)} place(s).")
        return EXIT_DIFFERENT
    print("The registry matches the servers.")
    return EXIT_MATCH


if __name__ == "__main__":
    sys.exit(main())
