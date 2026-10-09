"""Deployment preflight checks for starting the production shadow (T-078)."""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Literal

import httpx2
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
from temporalio.client import Client

from ais0c_activities import deploy, load_model_releases
from ais0c_worker import model_release

DATABASE_URL_ENV: Final = "AIS0C_DATABASE_URL"
GATEWAY_URL_ENV: Final = "AIS0C_GATEWAY_URL"
SECRETS_DIR_ENV: Final = "AIS0C_WORKER_SECRETS_DIR"
ROOT_ENV: Final = "AIS0C_WORKER_ROOT"
MODEL_REGISTRY_ENV: Final = "AIS0C_MODEL_REGISTRY"
SKILLS_MODE_ENV: Final = "AIS0C_SKILLS_MODE"
LITELLM_URL_ENV: Final = "LITELLM_BASE_URL"
LITELLM_KEY_ENV: Final = "LITELLM_API_KEY"
TEMPORAL_ADDRESS_ENV: Final = "TEMPORAL_ADDRESS"
TEMPORAL_NAMESPACE_ENV: Final = "TEMPORAL_NAMESPACE"

PROD_REGISTRY: Final = Path("config/models/registry.prod.yaml")
PROD_LITELLM_CONFIG: Final = Path("config/litellm/litellm.prod.yaml")
DEFAULT_TIMEOUT_SECONDS: Final = 10.0
CHECK_NAMES: Final = (
    "database",
    "shadow",
    "skills_mode",
    "skills",
    "model_registry",
    "gateway",
    "temporal",
    "models",
)

type Status = Literal["PASS", "FAIL", "WARN"]
type TemporalProbe = Callable[[str, str], Awaitable[str]]
type ReleaseVerifier = Callable[[Path, Path, Mapping[str, str]], list[str] | int]


@dataclass(frozen=True)
class CheckResult:
    """One stable, serializable preflight result."""

    status: Status
    name: str
    detail: str


def run_checks(
    environ: Mapping[str, str],
    *,
    skip_models: bool = False,
    temporal_probe: TemporalProbe | None = None,
    release_verifier: ReleaseVerifier | None = None,
) -> list[CheckResult]:
    """Run all eight checks in deployment order, without stopping after a failure."""
    probe = temporal_namespace if temporal_probe is None else temporal_probe
    verifier = model_release.verify if release_verifier is None else release_verifier
    return [
        _database(environ),
        _shadow(environ),
        _skills_mode(environ),
        _skills(environ),
        _model_registry(environ, verifier),
        _gateway(environ),
        _temporal(environ, probe),
        _models(environ, skip=skip_models),
    ]


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    """Run the preflight command and return 1 exactly when any check fails."""
    parser = argparse.ArgumentParser(prog="python -m ais0c_worker preflight")
    parser.add_argument("--skip-models", action="store_true", help="do not call model aliases")
    parser.add_argument("--json", action="store_true", help="write the check list as JSON")
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    results = run_checks(env, skip_models=args.skip_models)
    failed = sum(result.status == "FAIL" for result in results)
    if args.json:
        print(json.dumps([asdict(result) for result in results], ensure_ascii=False))
    else:
        for result in results:
            print(f"{result.status} {result.name:<14} {result.detail}")
        print(f"preflight: {failed} failed")
    return 1 if failed else 0


def _database(env: Mapping[str, str]) -> CheckResult:
    try:
        current, head = deploy.database_revision(_required(env, DATABASE_URL_ENV))
    except Exception as error:  # noqa: BLE001 - every prerequisite failure becomes a result.
        return _failure("database", "cannot connect", error)
    if current == head:
        return CheckResult("PASS", "database", f"at head {head}")
    return CheckResult("FAIL", "database", f"behind: {current or 'none'} -> {head}")


def _shadow(env: Mapping[str, str]) -> CheckResult:
    try:
        enabled = deploy.writes_enabled(_required(env, DATABASE_URL_ENV))
    except Exception as error:  # noqa: BLE001 - every prerequisite failure becomes a result.
        return _failure("shadow", "cannot read writes_enabled", error)
    if enabled:
        return CheckResult("FAIL", "shadow", "writes_enabled is on")
    return CheckResult("PASS", "shadow", "writes_enabled is off")


def _skills_mode(env: Mapping[str, str]) -> CheckResult:
    mode = env.get(SKILLS_MODE_ENV, "").strip() or "prod"
    if mode == "prod":
        return CheckResult("PASS", "skills_mode", "prod")
    return CheckResult("FAIL", "skills_mode", f"{mode}; prod is required")


def _skills(env: Mapping[str, str]) -> CheckResult:
    root = Path(env.get(ROOT_ENV, ".") or ".")
    try:
        count = deploy.approved_skill_count(root)
    except Exception as error:  # noqa: BLE001 - every prerequisite failure becomes a result.
        return _failure("skills", "cannot load approved skills", error)
    if count == 0:
        return CheckResult("WARN", "skills", "no approved skill; shadow runs without skills")
    suffix = "skill" if count == 1 else "skills"
    return CheckResult("PASS", "skills", f"{count} approved {suffix}")


def _model_registry(env: Mapping[str, str], verifier: ReleaseVerifier) -> CheckResult:
    configured = env.get(MODEL_REGISTRY_ENV, "").strip()
    if configured != str(PROD_REGISTRY):
        shown = configured or "not set"
        return CheckResult("FAIL", "model_registry", f"{shown}; expected {PROD_REGISTRY}")
    root = Path(env.get(ROOT_ENV, ".") or ".")
    try:
        verified = verifier(root / PROD_REGISTRY, root / PROD_LITELLM_CONFIG, env)
    except Exception as error:  # noqa: BLE001 - every prerequisite failure becomes a result.
        return _failure("model_registry", "verify failed", error)
    if isinstance(verified, int):
        if verified != 0:
            return CheckResult("FAIL", "model_registry", f"verify exited {verified} (H-7)")
    elif verified:
        extra = f" (+{len(verified) - 1} more)" if len(verified) > 1 else ""
        return CheckResult("FAIL", "model_registry", f"verify: {verified[0]}{extra} (H-7)")
    return CheckResult("PASS", "model_registry", "verify passed (H-7)")


def _gateway(env: Mapping[str, str]) -> CheckResult:
    try:
        base_url = _required(env, GATEWAY_URL_ENV).rstrip("/")
        secrets_dir = Path(env.get(SECRETS_DIR_ENV, "/run/secrets") or "/run/secrets")
        token_files = sorted(secrets_dir.glob("gateway-token-*"))
        if not token_files:
            raise ValueError("no gateway-token-<profile> files")
        with httpx2.Client(timeout=DEFAULT_TIMEOUT_SECONDS, follow_redirects=False) as client:
            health = client.get(f"{base_url}/healthz")
            if health.status_code != 200:
                raise ValueError(f"healthz answered HTTP {health.status_code}")
            for token_file in token_files:
                profile = token_file.name.removeprefix("gateway-token-")
                token = token_file.read_text(encoding="utf-8").strip()
                if not token:
                    raise ValueError(f"{token_file.name} is empty")
                response = client.get(
                    f"{base_url}/v1/tools",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if response.status_code != 200:
                    raise ValueError(f"{profile} answered HTTP {response.status_code}")
                answer = response.json()
                if not isinstance(answer, dict) or answer.get("name") != profile:
                    raise ValueError(f"{profile} token does not serve that profile")
                tools = answer.get("tools")
                if not isinstance(tools, list) or not tools:
                    raise ValueError(f"{profile} has no tools")
    except Exception as error:  # noqa: BLE001 - every prerequisite failure becomes a result.
        return _failure("gateway", "unavailable or invalid", error)
    return CheckResult("PASS", "gateway", f"{len(token_files)} profiles, every tool list non-empty")


def _temporal(env: Mapping[str, str], probe: TemporalProbe) -> CheckResult:
    address = env.get(TEMPORAL_ADDRESS_ENV, "").strip() or "127.0.0.1:7233"
    namespace = env.get(TEMPORAL_NAMESPACE_ENV, "").strip() or "default"
    try:
        found = asyncio.run(_run_probe(probe, address, namespace))
    except Exception as error:  # noqa: BLE001 - every prerequisite failure becomes a result.
        return _failure("temporal", "cannot see namespace", error)
    return CheckResult("PASS", "temporal", found)


async def _run_probe(probe: TemporalProbe, address: str, namespace: str) -> str:
    return await probe(address, namespace)


async def temporal_namespace(address: str, namespace: str) -> str:
    async def describe() -> str:
        client = await Client.connect(address, namespace=namespace)
        await client.workflow_service.describe_namespace(
            DescribeNamespaceRequest(namespace=namespace)
        )
        return namespace

    return await asyncio.wait_for(describe(), timeout=DEFAULT_TIMEOUT_SECONDS)


def _models(env: Mapping[str, str], *, skip: bool) -> CheckResult:
    if skip:
        return CheckResult("WARN", "models", "skipped")
    try:
        root = Path(env.get(ROOT_ENV, ".") or ".")
        registry = _required(env, MODEL_REGISTRY_ENV)
        aliases = load_model_releases(root / registry)
        base_url = _required(env, LITELLM_URL_ENV).rstrip("/")
        key = env.get(LITELLM_KEY_ENV, "").strip()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        with httpx2.Client(timeout=DEFAULT_TIMEOUT_SECONDS, follow_redirects=False) as client:
            for alias in aliases:
                response = client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": alias,
                        "messages": [{"role": "user", "content": "Reply with one token."}],
                        "max_tokens": 1,
                    },
                )
                if response.status_code != 200:
                    raise ValueError(f"{alias} answered HTTP {response.status_code}")
                response.json()
    except Exception as error:  # noqa: BLE001 - every prerequisite failure becomes a result.
        return _failure("models", "an alias did not answer", error)
    return CheckResult("PASS", "models", f"{len(aliases)} aliases answered")


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is not set")
    return value


def _failure(name: str, detail: str, error: Exception) -> CheckResult:
    reason = str(error).replace("\n", " ").strip() or type(error).__name__
    return CheckResult("FAIL", name, f"{detail}: {reason}")


if __name__ == "__main__":
    sys.exit(main())
