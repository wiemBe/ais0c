"""What the case and batch workers run with, built from their configuration and environment.

| Variable | Meaning | Default |
|---|---|---|
| `AIS0C_DATABASE_URL` | Application database (`ais0c_storage`) | none |
| `AIS0C_GATEWAY_URL` | MCP Policy Gateway, e.g. `http://127.0.0.1:8090` | none |
| `AIS0C_WORKER_SECRETS_DIR` | Directory of the secret file `gateway-token-<profile>` | `/run/secrets` |
| `AIS0C_WORKER_ROOT` | Directory with `config/` and `prompts/` | `.` |
| `AIS0C_MODEL_REGISTRY` | Model registry under the root: `config/models/registry.<env>.yaml` | none |
| `LITELLM_BASE_URL`, `LITELLM_API_KEY` | LiteLLM (`ais0c_agents.llm`) | none |
| `AIS0C_MAX_CONCURRENT_CASES` and the rest of `CaseSettings` | intake and SLA | §9 |

The case worker (`load_case_runtime`, T-012): the Triage agent's manifest is
`config/agents/triage.yaml`; its model alias goes to LiteLLM, so the code never names a model
provider (AGENTS.md hard rule 3). The model registry also gives the release of each alias
(T-24), which agent runs record. The worker holds one gateway token, the one of the manifest's
profile: the agent and the offense source both read with it.

The batch worker (`load_batch_runtime`, T-022) runs KnowledgeSync's catalog sync. It needs the
database, the gateway and the token of the `qradar-inventory-read` profile; the root, the
model registry and LiteLLM are not used.

The tools, their descriptions and schemas come from the gateway (`GET /v1/tools`), which reads
them from the platform's registry, never from the MCP server (architecture §13.3).
"""

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from ais0c_activities.catalog import INVENTORY_PROFILE, INVENTORY_TOOLS, CatalogSyncActivities
from ais0c_activities.db import SessionFactory
from ais0c_activities.enrichment import IocMatcher
from ais0c_activities.gateway_source import GatewayOffenseSource
from ais0c_activities.model_release import load_model_releases
from ais0c_activities.offense_source import OffenseSource
from ais0c_activities.settings import CaseSettings
from ais0c_activities.triage import TriageRuntime
from ais0c_agents import build_model, load_agent_prompt, load_manifest, load_model_registry
from ais0c_agents.gateway_http import HttpGatewayClient
from ais0c_contracts import ModelRelease
from ais0c_storage import create_engine, create_session_factory, database_url

GATEWAY_URL_ENV: Final = "AIS0C_GATEWAY_URL"
SECRETS_DIR_ENV: Final = "AIS0C_WORKER_SECRETS_DIR"
ROOT_ENV: Final = "AIS0C_WORKER_ROOT"
MODEL_REGISTRY_ENV: Final = "AIS0C_MODEL_REGISTRY"
TRIAGE_MANIFEST: Final = "config/agents/triage.yaml"
MIN_TOKEN_LENGTH: Final = 32
_TOKEN = re.compile(r"[\x21-\x7e]+")


class RuntimeConfigError(ValueError):
    """A setting, file or secret the worker needs is missing or invalid."""


@dataclass(frozen=True)
class CaseRuntime:
    sessions: SessionFactory
    source: OffenseSource
    triage: TriageRuntime
    settings: CaseSettings
    model_releases: Mapping[str, ModelRelease]
    """The release of every alias in the model registry."""
    ioc_matcher: IocMatcher | None = None
    engine: AsyncEngine | None = None
    """The database engine behind `sessions`, when the runtime created it."""

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()


async def load_case_runtime(environ: Mapping[str, str] | None = None) -> CaseRuntime:
    """Build the runtime from `environ` (default `os.environ`).

    Asks the gateway for the profile's tools, so the gateway must be up; fails on a missing or
    invalid setting rather than run with less.
    """
    env = os.environ if environ is None else environ
    root = Path(env.get(ROOT_ENV, ".") or ".")
    registry_path = root / _required(env, MODEL_REGISTRY_ENV)
    registry = load_model_registry(registry_path)
    releases = load_model_releases(registry_path)
    manifest = load_manifest(root / TRIAGE_MANIFEST, registry)
    if manifest.toolset_profile is None:
        raise RuntimeConfigError(f"{TRIAGE_MANIFEST} names no toolset profile")
    secrets_dir = Path(env.get(SECRETS_DIR_ENV, "/run/secrets") or "/run/secrets")
    gateway = HttpGatewayClient(
        _required(env, GATEWAY_URL_ENV),
        read_token(secrets_dir / f"gateway-token-{manifest.toolset_profile}"),
    )
    profile = await gateway.fetch_toolset()
    engine = create_engine(database_url(env))
    sessions = create_session_factory(engine)
    triage = TriageRuntime.build(
        manifest=manifest,
        prompt=load_agent_prompt(root, manifest),
        profile=profile,
        gateway=gateway,
        model=build_model(
            manifest.model_alias,
            settings=registry[manifest.model_alias].model_settings(),
            environ=env,
            forced_tool_choice=registry[manifest.model_alias].forced_tool_choice,
        ),
        # load_manifest found the alias in the registry, and every entry has a release.
        model_release=releases[manifest.model_alias],
    )
    return CaseRuntime(
        sessions=sessions,
        source=GatewayOffenseSource(gateway=gateway, profile=profile, sessions=sessions),
        triage=triage,
        settings=CaseSettings.from_env(env),
        model_releases=releases,
        engine=engine,
    )


@dataclass(frozen=True)
class BatchRuntime:
    """What the `soc-batch` worker runs with: KnowledgeSync's catalog sync."""

    sessions: SessionFactory
    catalog_sync: CatalogSyncActivities
    engine: AsyncEngine | None = None
    """The database engine behind `sessions`, when the runtime created it."""

    def activities(self) -> list[Callable[..., object]]:
        """Every activity of the `soc-batch` task queue, ready to register on a worker."""
        return self.catalog_sync.activities()

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()


async def load_batch_runtime(environ: Mapping[str, str] | None = None) -> BatchRuntime:
    """Build the batch worker's runtime from `environ` (default `os.environ`).

    Asks the gateway for the tools of the token's profile, so the gateway must be up; fails
    unless the token is one of `qradar-inventory-read` with the lists the sync reads.
    """
    env = os.environ if environ is None else environ
    url = database_url(env)
    secrets_dir = Path(env.get(SECRETS_DIR_ENV, "/run/secrets") or "/run/secrets")
    gateway = HttpGatewayClient(
        _required(env, GATEWAY_URL_ENV),
        read_token(secrets_dir / f"gateway-token-{INVENTORY_PROFILE}"),
    )
    profile = await gateway.fetch_toolset()
    missing = INVENTORY_TOOLS - {tool.id for tool in profile.tools}
    if profile.name != INVENTORY_PROFILE or missing:
        lacking = f" without {', '.join(sorted(missing))}" if missing else ""
        raise RuntimeConfigError(
            f"the catalog sync needs {INVENTORY_PROFILE} with "
            f"{', '.join(sorted(INVENTORY_TOOLS))}; the gateway serves {profile.name}{lacking}"
        )
    engine = create_engine(url)
    sessions = create_session_factory(engine)
    return BatchRuntime(
        sessions=sessions,
        catalog_sync=CatalogSyncActivities(sessions=sessions, gateway=gateway, profile=profile),
        engine=engine,
    )


def read_token(path: Path) -> SecretStr:
    """A gateway token from a secret file: printable ASCII, at least 32 characters."""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise RuntimeConfigError(f"secret file {path.name} cannot be read") from None
    if len(value) < MIN_TOKEN_LENGTH or not _TOKEN.fullmatch(value):
        raise RuntimeConfigError(
            f"secret file {path.name} must hold one token of at least {MIN_TOKEN_LENGTH} "
            "printable characters"
        )
    return SecretStr(value)


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise RuntimeConfigError(f"{name} is not set")
    return value
