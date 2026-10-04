"""Checks against the running dev stack (T-003 acceptance criteria 1-5).

Opt-in, because they need Docker and a running stack. From the repository root:

    docker compose -f deploy/compose/docker-compose.dev.yaml up -d --wait
    AIS0C_DEV_STACK=1 uv run pytest tests/deploy/test_dev_stack.py

The LiteLLM smoke test runs only when OPENROUTER_API_KEY is set as well. The prod LiteLLM
configuration is loaded into the pinned LiteLLM image in a container without network access.
With COMPOSE_PROFILES=qradar the services of that profile (T-018) must be healthy too.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deploy/compose/docker-compose.dev.yaml"
SMOKE_SCRIPT = REPO_ROOT / "deploy/compose/smoke_litellm.py"
ALIASES = {"soc-fast", "soc-reasoning", "soc-verifier", "soc-report"}
MASTER_KEY = "sk-t003-prod-config-check"

pytestmark = pytest.mark.skipif(
    os.environ.get("AIS0C_DEV_STACK") != "1",
    reason="dev stack test: start deploy/compose/docker-compose.dev.yaml and set AIS0C_DEV_STACK=1",
)


def docker() -> str:
    path = shutil.which("docker")
    assert path is not None, "docker is not installed"
    return path


def compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [docker(), "compose", "-f", str(COMPOSE_FILE), *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def compose_ok(*args: str) -> str:
    result = compose(*args)
    assert result.returncode == 0, result.stderr
    return result.stdout


def compose_services() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))["services"]


def psql(
    sql: str, *, user: str = "postgres", database: str = "postgres"
) -> subprocess.CompletedProcess[str]:
    """Run one SQL statement in the postgres container, over the socket the image trusts."""
    return compose(
        "exec", "-T", "postgres", "psql", "--no-psqlrc", "--set=ON_ERROR_STOP=1",
        "--tuples-only", "--no-align", f"--username={user}", f"--dbname={database}", f"--command={sql}",
    )  # fmt: skip


def http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=headers or {})  # noqa: S310 - localhost URL
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read().decode()


# --- criterion 1: every service passes its healthcheck ------------------------------------


def test_every_service_is_healthy() -> None:
    services = compose_services()
    jobs = {
        dependency
        for service in services.values()
        for dependency, condition in (service.get("depends_on") or {}).items()
        if condition["condition"] == "service_completed_successfully"
    }
    output = compose_ok("ps", "--all", "--format", "json")
    containers = {c["Service"]: c for c in map(json.loads, output.splitlines())}
    # The services `up` starts: those without a profile and those of the profiles in
    # COMPOSE_PROFILES, such as T-018's "qradar". A profile's running containers are listed
    # even when COMPOSE_PROFILES leaves it out; they must be healthy too.
    started = set(compose_ok("config", "--services").split())

    assert started <= set(containers) <= set(services)
    for name, container in containers.items():
        if name in jobs:
            assert (container["State"], container["ExitCode"]) == ("exited", 0), name
        else:
            assert (container["State"], container["Health"]) == ("running", "healthy"), name


# --- criterion 2: Temporal, its UI and the default namespace ------------------------------


def temporal(*args: str) -> str:
    return compose_ok("exec", "-T", "temporal-admin-tools", "temporal", *args)


def test_default_namespace_is_registered() -> None:
    described = json.loads(
        temporal("operator", "namespace", "describe", "--namespace", "default", "--output", "json")
    )

    assert described["namespaceInfo"]["state"] == "NAMESPACE_STATE_REGISTERED"


def test_temporal_starts_a_workflow_in_the_default_namespace() -> None:
    workflow = ("--namespace", "default", "--workflow-id", f"t003-probe-{uuid.uuid4().hex}")
    temporal(
        "workflow", "start", *workflow,
        "--type", "Probe", "--task-queue", "t003-probe", "--execution-timeout", "1m",
    )  # fmt: skip
    try:
        described = json.loads(temporal("workflow", "describe", *workflow, "--output", "json"))

        assert described["workflowExecutionInfo"]["status"] == "WORKFLOW_EXECUTION_STATUS_RUNNING"
    finally:
        temporal("workflow", "terminate", *workflow, "--reason", "T-003 dev stack test")


def test_temporal_ui_reaches_the_default_namespace() -> None:
    status, body = http_get("http://127.0.0.1:8080/api/v1/namespaces/default")

    assert status == 200
    assert json.loads(body)["namespaceInfo"]["state"] == "NAMESPACE_STATE_REGISTERED"


# --- criterion 3: separate databases, pgvector in ais0c -----------------------------------


def test_application_and_temporal_data_live_in_separate_databases() -> None:
    result = psql(
        "SELECT datname, pg_get_userbyid(datdba) FROM pg_database WHERE NOT datistemplate"
    )
    owners = dict(line.split("|") for line in result.stdout.split())

    assert owners["ais0c"] == "ais0c"
    assert owners["temporal"] == "temporal"
    assert owners["temporal_visibility"] == "temporal"


def test_application_role_can_use_pgvector_without_being_superuser() -> None:
    def as_app(sql: str) -> str:
        result = psql(sql, user="ais0c", database="ais0c")
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    as_app("CREATE EXTENSION IF NOT EXISTS vector")  # what a migration would run

    assert as_app("SELECT rolsuper FROM pg_roles WHERE rolname = current_user") == "f"
    assert as_app("SELECT '[1,2,3]'::vector <-> '[1,2,5]'::vector") == "2"


@pytest.mark.parametrize(
    ("user", "database"),
    [("temporal", "ais0c"), ("ais0c", "temporal"), ("ais0c", "temporal_visibility")],
)
def test_roles_cannot_open_each_others_databases(user: str, database: str) -> None:
    result = psql("SELECT 1", user=user, database=database)

    assert result.returncode != 0
    assert "does not have CONNECT privilege" in result.stderr


# --- criteria 4 and 5: LiteLLM ------------------------------------------------------------

LIST_MODELS = """
import json, os, urllib.request
request = urllib.request.Request(
    "http://127.0.0.1:4000/v1/models",
    headers={"Authorization": "Bearer " + os.environ["LITELLM_MASTER_KEY"]},
)
with urllib.request.urlopen(request, timeout=10) as response:
    print(json.dumps(sorted(model["id"] for model in json.load(response)["data"])))
"""

LIVENESS = """
import urllib.request
urllib.request.urlopen("http://127.0.0.1:4000/health/liveliness", timeout=3).close()
"""

CHAT = """
import json, os, sys, urllib.error, urllib.request
request = urllib.request.Request(
    "http://127.0.0.1:4000/v1/chat/completions",
    data=json.dumps({"model": sys.argv[1], "messages": [{"role": "user", "content": "ping"}]}).encode(),
    headers={"Authorization": "Bearer " + os.environ["LITELLM_MASTER_KEY"], "Content-Type": "application/json"},
)
try:
    urllib.request.urlopen(request, timeout=60)
except urllib.error.HTTPError as error:
    print(error.read().decode())
"""


def test_litellm_requires_its_master_key() -> None:
    status, _ = http_get("http://127.0.0.1:4000/v1/models")

    assert status == 401


def test_litellm_serves_exactly_the_model_aliases() -> None:
    # Runs inside the container, so the master key never leaves it.
    output = compose_ok("exec", "-T", "litellm", "python", "-c", LIST_MODELS)

    assert set(json.loads(output)) == ALIASES


@pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="LiteLLM smoke test: OPENROUTER_API_KEY not set",
)
def test_smoke_script_gets_an_answer_from_soc_fast() -> None:
    env = dict(os.environ)
    if not env.get("LITELLM_MASTER_KEY"):
        env["LITELLM_MASTER_KEY"] = compose_ok(
            "exec", "-T", "litellm", "printenv", "LITELLM_MASTER_KEY"
        ).strip()

    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SMOKE_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("OK: soc-fast answered"), result.stdout


@pytest.fixture(scope="module")
def prod_litellm() -> Iterator[Callable[..., str]]:
    """Run the pinned LiteLLM image with litellm.prod.yaml and no network.

    Only the DeepSeek endpoint is set; the Qwen variables are missing on purpose.
    """
    image = compose_services()["litellm"]["image"]
    config = REPO_ROOT / "config/litellm/litellm.prod.yaml"
    name = f"ais0c-t003-prod-litellm-{uuid.uuid4().hex[:8]}"
    started = subprocess.run(  # noqa: S603
        [
            docker(), "run", "--detach", "--rm", "--name", name, "--network", "none",
            "--user", "65534:65534", "--read-only", "--tmpfs", "/tmp",  # noqa: S108 - in the container
            "--env", f"LITELLM_MASTER_KEY={MASTER_KEY}",
            "--env", "LITELLM_LOCAL_MODEL_COST_MAP=True",
            "--env", "VLLM_DEEPSEEK_V4_FLASH_API_BASE=http://vllm-deepseek.invalid:8000/v1",
            "--volume", f"{config}:/etc/litellm/config.yaml:ro,z",
            image, "--config", "/etc/litellm/config.yaml", "--port", "4000",
        ],
        capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    assert started.returncode == 0, started.stderr

    def run_python(code: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [docker(), "exec", name, "python", "-c", code, *args],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def python(code: str, *args: str) -> str:
        result = run_python(code, *args)
        assert result.returncode == 0, result.stderr
        return result.stdout

    try:
        deadline = time.monotonic() + 90
        while run_python(LIVENESS).returncode != 0:
            assert time.monotonic() < deadline, "LiteLLM did not start with the prod configuration"
            time.sleep(2)
        yield python
    finally:
        subprocess.run([docker(), "rm", "--force", name], capture_output=True, check=False)  # noqa: S603


def test_prod_config_loads_in_the_pinned_litellm_image(prod_litellm: Callable[..., str]) -> None:
    assert set(json.loads(prod_litellm(LIST_MODELS))) == ALIASES


@pytest.mark.parametrize(
    ("alias", "host"),
    [("soc-fast", "vllm-deepseek.invalid"), ("soc-verifier", "vllm-api-base-not-set.invalid")],
    ids=["endpoint-set", "endpoint-missing"],
)
def test_prod_request_goes_only_to_the_configured_vllm(
    prod_litellm: Callable[..., str], alias: str, host: str
) -> None:
    # Without the guard in litellm.prod.yaml a missing endpoint would fall back to the public
    # OpenAI API (D-11).
    error = prod_litellm(CHAT, alias)

    assert host in error
    assert "openai.com" not in error


# --- OpenTelemetry collector --------------------------------------------------------------


def test_otel_collector_receives_traces() -> None:
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 1))
    now = str(time.time_ns())
    span = {"traceId": uuid.uuid4().hex, "spanId": uuid.uuid4().hex[:16], "name": "t003-probe",
            "kind": 1, "startTimeUnixNano": now, "endTimeUnixNano": now}  # fmt: skip
    trace = {"resourceSpans": [{"scopeSpans": [{"scope": {"name": "t003"}, "spans": [span]}]}]}
    request = urllib.request.Request(
        "http://127.0.0.1:4318/v1/traces",
        data=json.dumps(trace).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        assert response.status == 200

    deadline = time.monotonic() + 15
    while '"otelcol.signal": "traces"' not in compose_ok(
        "logs", "--since", since, "otel-collector"
    ):
        assert time.monotonic() < deadline, "the debug exporter logged no traces"
        time.sleep(1)
