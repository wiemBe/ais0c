"""Processes, settings and lab reads for the end-to-end lab test (tests/e2e/README.md).

Nothing here holds a secret: tokens come from the environment and are written only to the
test's temporary directory, which pytest removes.
"""

import os
import secrets
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Final

import httpx2
import yaml

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
MODEL_REGISTRY: Final = "config/models/registry.dev.yaml"
# The lab's test rule (tests/e2e/README.md, "Lab kuralı").
LAB_RULE_NAME: Final = "AIS0C LAB - DCSync by a non-machine account"
SCENARIO: Final = "s2-dcsync"
# Seeds whose three DCSync events share one account, so the rule opens one offense.
SINGLE_ACCOUNT_SEEDS: Final = {"9": "bkupadmin", "12": "svc_backup", "75": "svc_sql"}


class E2ESetupError(RuntimeError):
    """A prerequisite of the lab test is missing; the message says which."""


@dataclass(frozen=True)
class LabSettings:
    qradar_host: str
    qradar_token: str = field(repr=False)
    verify_ssl: bool
    fork_command: str
    database_url: str = field(repr=False)
    litellm_base_url: str
    litellm_api_key: str = field(repr=False)
    temporal_address: str
    syslog_target: str
    seed: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LabSettings":
        env = os.environ if env is None else env
        missing = [
            name
            for name in (
                "QRADAR_LAB_URL",
                "QRADAR_LAB_TOKEN",
                "AIS0C_E2E_QRADAR_MCP",
                "AIS0C_DATABASE_URL",
                "LITELLM_API_KEY",
            )
            if not env.get(name, "").strip()
        ]
        if missing:
            raise E2ESetupError(f"set {', '.join(missing)} (tests/e2e/README.md)")
        host = env["QRADAR_LAB_URL"].strip().removeprefix("https://").rstrip("/")
        seed = env.get("AIS0C_E2E_SEED", "9").strip()
        if seed not in SINGLE_ACCOUNT_SEEDS:
            raise E2ESetupError(f"AIS0C_E2E_SEED must be one of {', '.join(SINGLE_ACCOUNT_SEEDS)}")
        return cls(
            qradar_host=host,
            qradar_token=env["QRADAR_LAB_TOKEN"].strip(),
            verify_ssl=env.get("QRADAR_LAB_VERIFY_SSL", "true").strip().lower() != "false",
            fork_command=env["AIS0C_E2E_QRADAR_MCP"].strip(),
            database_url=env["AIS0C_DATABASE_URL"].strip(),
            litellm_base_url=env.get("LITELLM_BASE_URL", "").strip() or "http://127.0.0.1:4000",
            litellm_api_key=env["LITELLM_API_KEY"].strip(),
            temporal_address=env.get("TEMPORAL_ADDRESS", "").strip() or "127.0.0.1:7233",
            syslog_target=env.get("AIS0C_E2E_SYSLOG", "").strip() or f"{host.split(':')[0]}:514",
            seed=seed,
        )

    @property
    def attacker(self) -> str:
        """The account the scenario's DCSync events use with this seed."""
        return SINGLE_ACCOUNT_SEEDS[self.seed]


def triage_profile() -> str:
    """The gateway profile of the Triage agent's manifest (config/agents/triage.yaml)."""
    manifest = yaml.safe_load((REPO_ROOT / "config/agents/triage.yaml").read_text(encoding="utf-8"))
    profile = manifest.get("toolset_profile") if isinstance(manifest, dict) else None
    if not isinstance(profile, str):
        raise E2ESetupError("config/agents/triage.yaml names no toolset profile")
    return profile


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def write_secret(directory: Path, name: str, value: str) -> Path:
    path = directory / name
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def new_token() -> str:
    return secrets.token_urlsafe(32)


class Process:
    """A service the test starts; its output goes to a log file next to the test's data."""

    def __init__(
        self, name: str, command: list[str], env: Mapping[str, str], log_dir: Path
    ) -> None:
        self.name = name
        self._log_path = log_dir / f"{name}.log"
        self._log: IO[bytes] = self._log_path.open("ab")
        self._process = subprocess.Popen(  # noqa: S603 - the test's own commands
            command,
            env={**os.environ, **env},
            stdout=self._log,
            stderr=subprocess.STDOUT,
            cwd=REPO_ROOT,
        )

    @property
    def log_path(self) -> Path:
        return self._log_path

    def running(self) -> bool:
        return self._process.poll() is None

    def wait_http_ok(self, url: str, *, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.running():
                raise E2ESetupError(f"{self.name} exited; see {self._log_path}")
            try:
                with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - local
                    if response.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.5)
        raise E2ESetupError(f"{self.name} did not answer {url}; see {self._log_path}")

    def kill(self) -> None:
        """Stop at once, as a crash would: no shutdown, no goodbye to Temporal."""
        if self.running():
            self._process.send_signal(signal.SIGKILL)
            self._process.wait(timeout=30)

    def stop(self) -> None:
        if self.running():
            self._process.send_signal(signal.SIGTERM)
            try:
                self._process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=30)
        self._log.close()


async def qradar_get(
    settings: LabSettings, path: str, params: Mapping[str, str]
) -> list[dict[str, object]]:
    """A read of the lab QRadar's REST API, for the test's own preconditions only; the
    platform reads QRadar through the gateway."""
    async with httpx2.AsyncClient(
        base_url=f"https://{settings.qradar_host}/api",
        verify=settings.verify_ssl,
        timeout=60,
    ) as client:
        response = await client.get(
            path,
            params=dict(params),
            headers={
                "SEC": settings.qradar_token,
                "Version": "29.0",
                "Accept": "application/json",
            },
        )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise E2ESetupError(f"QRadar answered {path} with no list")
    return [item for item in data if isinstance(item, dict)]
