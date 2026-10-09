"""Processes, settings and lab reads for the end-to-end lab test (tests/e2e/README.md).

AIS0C_E2E_MODEL_REGISTRY selects the model registry (default registry.dev.yaml; T-104).

Nothing here holds a secret: tokens come from the environment and are written only to the
test's temporary directory, which pytest removes.
"""

import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Final

import httpx2
import yaml

from ais0c_harness.loggen.scenario import generate, load_scenario

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
MODEL_REGISTRY: Final = os.environ.get(
    "AIS0C_E2E_MODEL_REGISTRY", "config/models/registry.dev.yaml"
)
LAB_RULES_DIR: Final = REPO_ROOT / "harness" / "lab" / "qradar" / "rules"
DEFAULT_SCENARIO: Final = "s2-dcsync"
# Seeds the DCSync scenario's three events share one account for, so its rule opens one offense.
# Seed 9 is the default and also the default of the other scenarios, whose key does not vary.
SINGLE_ACCOUNT_SEEDS: Final = {"9": "bkupadmin", "12": "svc_backup", "75": "svc_sql"}


@dataclass(frozen=True)
class ScenarioSpec:
    """A lab scenario the chain test can run (T-058): what it should decide, where its offense's
    key comes from and which lab rule opens the offense.

    The expected decision is a measurement reference, never an assertion: the report prints it
    next to what the chain decided.
    """

    name: str
    expected_verdict: str
    expected_level: str
    rationale: str
    #: The steps whose events carry the offense's key (a user name or a source address).
    key_steps: tuple[str, ...]

    def lab_rule(self) -> tuple[str, str]:
        """The name of the lab rule for this scenario and what it indexes offenses by
        (`username` or `source_ip`), from the rule sources under harness/lab/qradar/rules."""
        for path in sorted(LAB_RULES_DIR.glob("*.yaml")):
            rule = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(rule, dict) and rule.get("scenario") == self.name:
                return str(rule["name"]), str(rule["index_by"])
        raise E2ESetupError(f"no lab rule source for {self.name} under {LAB_RULES_DIR}")

    def offense_key(self, seed: int) -> str:
        """The user name or source address the offense is indexed by with this seed. The
        scenario's key steps must agree on one value, or the rule would open several offenses."""
        _, index_by = self.lab_rule()
        scenario = load_scenario(self.name)
        events = generate(scenario, seed=seed, base_time=datetime(2026, 1, 1, tzinfo=UTC))
        keys = {
            generated.event.username if index_by == "username" else generated.event.src
            for generated in events
            if generated.label.step in self.key_steps
        }
        if len(keys) != 1:
            raise E2ESetupError(
                f"{self.name} with seed {seed} has {len(keys)} offense keys ({sorted(keys)}); "
                "the lab rule would open one offense per key"
            )
        return keys.pop()

    def comparison(self, verdict: str, level: str) -> dict[str, str]:
        """The scenario's expected decision and the chain's, side by side, for the report."""
        return {
            "scenario": self.name,
            "expected_verdict": self.expected_verdict,
            "expected_level": self.expected_level,
            "chain_verdict": verdict,
            "chain_level": level,
            "rationale": self.rationale,
        }


# The decisions are the scenario files' headers (harness/scenarios/s*.yaml) and T-78.
SCENARIO_SPECS: Final = {
    spec.name: spec
    for spec in (
        ScenarioSpec(
            "s2-dcsync",
            "tp",
            "high",
            "A non-machine account used directory-replication rights on a domain controller.",
            ("dcsync-attack",),
        ),
        ScenarioSpec(
            "s4-kerberoasting",
            "tp",
            "high",
            "One account asked for RC4 tickets of eight service accounts in half a minute.",
            ("kerberoast-requests",),
        ),
        ScenarioSpec(
            "s5-password-spraying",
            "tp",
            "high",
            "One source failed logons on twelve accounts, then one logon succeeded.",
            ("spray-failed-logons", "spray-kerberos-preauth-failures", "spray-success"),
        ),
        ScenarioSpec(
            "s6-waf-sqli-gecti",
            "tp",
            "high",
            "SQL injection that the WAF only alerted on; the application answered it.",
            ("sqli-not-blocked",),
        ),
        ScenarioSpec(
            "s7-waf-xss-gecti",
            "tp or suspicious",
            "medium",
            "Cross-site scripting that the WAF only alerted on.",
            ("xss-not-blocked",),
        ),
        ScenarioSpec(
            "s8-waf-tarama-engellendi",
            "tp",
            "low",
            "An external scanner whose every request the WAF blocked: still an attack, no impact.",
            ("external-scan-blocked",),
        ),
        ScenarioSpec(
            "s9-onayli-tarayici",
            "fp",
            "low",
            "The bank's own scanner in its maintenance window, every request blocked.",
            ("approved-scan-blocked",),
        ),
    )
}


def loggen_command(
    settings: "LabSettings", out_dir: Path, *, python: str = sys.executable
) -> list[str]:
    """The generator's command line for the selected scenario (T-008, T-058)."""
    return [
        python,
        "-m",
        "ais0c_harness.loggen",
        "run",
        "--scenario",
        settings.scenario,
        "--target",
        settings.syslog_target,
        "--seed",
        settings.seed,
        "--speed",
        "100000",
        "--out-dir",
        str(out_dir),
    ]


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
    scenario: str

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
        scenario = env.get("AIS0C_E2E_SCENARIO", "").strip() or DEFAULT_SCENARIO
        if scenario not in SCENARIO_SPECS:
            raise E2ESetupError(
                f"AIS0C_E2E_SCENARIO must be one of {', '.join(SCENARIO_SPECS)}, not {scenario!r}"
            )
        seed = env.get("AIS0C_E2E_SEED", "9").strip()
        if not seed.isdecimal():
            raise E2ESetupError("AIS0C_E2E_SEED must be a number")
        # One offense key per run: for DCSync only some seeds give the three events one account.
        SCENARIO_SPECS[scenario].offense_key(int(seed))
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
            scenario=scenario,
        )

    @property
    def spec(self) -> ScenarioSpec:
        return SCENARIO_SPECS[self.scenario]

    @property
    def rule_name(self) -> str:
        """The lab rule that opens this scenario's offense."""
        return self.spec.lab_rule()[0]

    @property
    def offense_key(self) -> str:
        """The user name or source address this run's offense is indexed by."""
        return self.spec.offense_key(int(self.seed))


def agent_profile(agent: str) -> str:
    """The gateway profile of an agent's manifest (config/agents/<agent>.yaml)."""
    path = f"config/agents/{agent}.yaml"
    manifest = yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))
    profile = manifest.get("toolset_profile") if isinstance(manifest, dict) else None
    if not isinstance(profile, str):
        raise E2ESetupError(f"{path} names no toolset profile")
    return profile


# The case worker's agents with tools: it holds a gateway token for each one's profile.
AGENTS_WITH_TOOLS = ("triage", "investigation", "verification")


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
