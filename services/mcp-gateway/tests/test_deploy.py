"""The gateway in the dev stack: Compose profile "qradar" (T-018 criteria 1, 2, 4 and 6).

Static checks of deploy/compose/docker-compose.dev.yaml, the lab override
docker-compose.lab.yaml, the gateway's Dockerfile and deploy/compose/make_secrets.py. The rules
every service follows (pinned images, healthchecks, ports on localhost, secrets) are T-003's,
in tests/deploy/test_compose_file.py. How the stack was run against the lab QRadar is in the
T-018 PR.
"""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from gateway_support import CONFIG_DIR, REPO_ROOT

from ais0c_mcp_gateway.service import build_service
from ais0c_mcp_gateway.settings import Settings

COMPOSE_DIR = REPO_ROOT / "deploy/compose"
DEV_FILE = COMPOSE_DIR / "docker-compose.dev.yaml"
LAB_FILE = COMPOSE_DIR / "docker-compose.lab.yaml"
DOCKERFILE = REPO_ROOT / "services/mcp-gateway/Dockerfile"
FORK_DIR = REPO_ROOT / "services/qradar-mcp"
PROD_FILE = COMPOSE_DIR / "docker-compose.prod.yaml"
GATEWAY = "mcp-gateway"
# Compose service (= MCP instance) and the fork --profile it runs.
MCP_INSTANCES = {"qradar-mcp-read": "qradar-read", "qradar-mcp-note": "qradar-note"}
QRADAR_TOKENS = {"qradar-mcp-read": "qradar-token-read", "qradar-mcp-note": "qradar-token-note"}
EXECUTOR_MOUNT = "gateway-token-qradar-note-write"
SYNTHETIC_QRADAR = {
    "AIS0C_QRADAR_READ_TOKEN": "synthetic-qradar-read-token",
    "AIS0C_QRADAR_NOTE_TOKEN": "synthetic-qradar-note-token",
}


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def dev() -> dict[str, Any]:
    return load(DEV_FILE)


def manifest() -> dict[str, Any]:
    return load(CONFIG_DIR / "connectors/qradar.yaml")


def networks_of(service: dict[str, Any]) -> set[str]:
    return set(service.get("networks") or ["default"])


def mounted_secrets(service: dict[str, Any]) -> set[str]:
    return set(service.get("secrets") or [])


def option(command: list[str], name: str) -> str:
    """The value of the last `name` option, as argparse reads it."""
    return command[len(command) - 1 - command[::-1].index(name) + 1]


# --- criterion 1: the gateway's image and service -------------------------------------------


def test_the_gateway_service_is_built_here_and_has_a_healthcheck() -> None:
    gateway = dev()["services"][GATEWAY]

    assert gateway["profiles"] == ["qradar"]
    assert (COMPOSE_DIR / gateway["build"]["context"]).resolve() == REPO_ROOT
    assert REPO_ROOT / gateway["build"]["dockerfile"] == DOCKERFILE
    assert gateway["pull_policy"] == "build"
    assert gateway["healthcheck"]["test"][:3] == ["CMD", "python", "-c"]
    assert "http://127.0.0.1:8080/healthz" in gateway["healthcheck"]["test"][3]
    assert gateway["depends_on"] == {"postgres": {"condition": "service_healthy"}}
    assert gateway["ports"] == ["127.0.0.1:8090:8080"]
    assert (gateway["read_only"], gateway["cap_drop"]) == (True, ["ALL"])


def test_the_gateway_reads_the_platform_config_files() -> None:
    gateway = dev()["services"][GATEWAY]
    config_dir = gateway["environment"]["AIS0C_GATEWAY_CONFIG_DIR"]

    assert gateway["volumes"] == [
        f"../../config/connectors:{config_dir}/connectors:ro,z",
        f"../../config/policies:{config_dir}/policies:ro,z",
    ]


def dockerfile_stages() -> list[list[str]]:
    """The Dockerfile's instructions, one list per stage, continuation lines joined."""
    stages: list[list[str]] = []
    for line in DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ").splitlines():
        line = " ".join(line.split())
        if not line or line.startswith("#"):
            continue
        if line.startswith("FROM "):
            stages.append([])
        stages[-1].append(line)
    return stages


def test_the_image_runs_only_the_gateway_without_root() -> None:
    final = dockerfile_stages()[-1]

    assert final[-1] == 'ENTRYPOINT ["python", "-m", "ais0c_mcp_gateway"]'
    users = [line.split()[1] for line in final if line.startswith("USER ")]
    assert users == ["10001:10001"]
    assert any(line.startswith("HEALTHCHECK ") and "/healthz" in line for line in final)
    # Only the built environment and the release's connectors and policies (T-077) reach the
    # final image: no source and no secret.
    assert [line for line in final if line.startswith(("COPY ", "ADD "))] == [
        "COPY --from=build /opt/venv /opt/venv",
        "COPY config/connectors /etc/ais0c/connectors",
        "COPY config/policies /etc/ais0c/policies",
    ]


def test_the_build_context_is_only_the_workspace_and_the_gateways_packages() -> None:
    lines = (DOCKERFILE.parent / "Dockerfile.dockerignore").read_text(encoding="utf-8")
    patterns = [line for line in lines.splitlines() if line and not line.startswith("#")]
    included = [pattern[1:] for pattern in patterns if pattern.startswith("!")]

    assert patterns[0] == "*"
    assert sorted(path for path in included if path.endswith("/src")) == [
        "packages/contracts/src",
        "packages/policy/src",
        "packages/storage/src",
        "services/mcp-gateway/src",
    ]
    assert all(
        path.endswith(("/src", "pyproject.toml", "uv.lock", ".python-version"))
        or path in {"config/connectors", "config/policies"}
        for path in included
    )


# --- criterion 2: the MCP instances ----------------------------------------------------------


def test_the_mcp_instances_run_the_forks_profiles_from_the_checkout_image() -> None:
    connector = manifest()
    services = dev()["services"]

    for name, fork_profile in MCP_INSTANCES.items():
        service = services[name]
        assert connector["server_profiles"][fork_profile]["instance"] == name
        # The fork is built from this checkout (D-46); the tag is not the fork commit.
        assert service["image"] == "qradar-mcp-fork:dev"
        assert (COMPOSE_DIR / service["build"]["context"]).resolve() == FORK_DIR
        assert service["pull_policy"] == "build"
        assert option(service["command"], "--profile") == fork_profile
        assert service["profiles"] == ["qradar"]
        assert (service["read_only"], service["cap_drop"]) == (True, ["ALL"])


def test_the_prod_fork_image_is_tagged_with_the_release_version() -> None:
    services = load(PROD_FILE)["services"]

    for name in MCP_INSTANCES:
        assert services[name]["image"].startswith("qradar-mcp-fork:${AIS0C_VERSION:?")
        assert services[name]["pull_policy"] == "never"
        assert "build" not in services[name]


def test_upstream_file_names_the_server_version() -> None:
    lines = (FORK_DIR / "UPSTREAM").read_text(encoding="utf-8").splitlines()
    fork_line = next(line for line in lines if line.startswith("fork_commit: "))

    assert fork_line.removeprefix("fork_commit: ").strip() == manifest()["server_version"]


def test_qradar_tokens_are_read_from_secret_files() -> None:
    compose = dev()

    for name, qradar_token in QRADAR_TOKENS.items():
        service = compose["services"][name]
        environment = service["environment"]
        assert environment["QRADAR_AUTH_TOKEN_FILE"] == f"/run/secrets/{qradar_token}"
        assert environment["MCP_AUTH_TOKEN_FILE"] == f"/run/secrets/mcp-token-{name}"
        assert {"QRADAR_AUTH_TOKEN", "MCP_AUTH_TOKEN"}.isdisjoint(environment)
        assert mounted_secrets(service) == {qradar_token, f"mcp-token-{name}"}
    for secret in compose["secrets"].values():
        assert secret["file"].startswith("./secrets/")


def test_the_secret_files_are_ignored_by_git() -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    paths = [
        str((COMPOSE_DIR / secret["file"]).relative_to(REPO_ROOT))
        for secret in dev()["secrets"].values()
    ]

    checked = subprocess.run(  # noqa: S603
        [git, "check-ignore", "--no-index", *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert sorted(checked.stdout.split()) == sorted(paths)


def test_only_the_gateway_shares_a_network_with_the_mcp_instances() -> None:
    compose = dev()
    services = compose["services"]

    assert compose["networks"]["mcp"] == {"internal": True}
    assert {name for name, s in services.items() if "mcp" in networks_of(s)} == {
        GATEWAY,
        *MCP_INSTANCES,
    }
    assert {name for name, s in services.items() if "qradar-egress" in networks_of(s)} == set(
        MCP_INSTANCES
    )
    assert networks_of(services[GATEWAY]) == {"default", "mcp"}
    for name in MCP_INSTANCES:
        service = services[name]
        alias = f"{name}.mcp"
        assert networks_of(service) == {"mcp", "qradar-egress"}
        assert service["networks"]["mcp"] == {"aliases": [alias]}
        assert not {"ports", "expose", "network_mode"} & service.keys()
        # The server listens only on its name on the mcp network, not on 0.0.0.0.
        assert option(service["command"], "--host") == alias
        assert f"http://{alias}:5000/healthz" in service["healthcheck"]["test"][3]
        variable = "AIS0C_GATEWAY_UPSTREAM_URL_" + name.upper().replace("-", "_")
        assert services[GATEWAY]["environment"][variable] == f"http://{alias}:5000/mcp"


# --- criterion 4 and §13.4: who holds which token --------------------------------------------


def test_each_secret_is_mounted_only_where_it_belongs() -> None:
    compose = dev()
    connector = manifest()
    instances = {profile["instance"] for profile in connector["server_profiles"].values()}
    holders: dict[str, set[str]] = {}
    for name, service in compose["services"].items():
        for secret in mounted_secrets(service):
            holders.setdefault(secret, set()).add(name)

    # The gateway: every profile's token and its token towards each instance; no QRadar token.
    assert mounted_secrets(compose["services"][GATEWAY]) == {
        f"gateway-token-{profile}" for profile in connector["profiles"]
    } | {f"mcp-token-{instance}" for instance in instances}
    for instance in instances:
        assert holders[f"mcp-token-{instance}"] == {GATEWAY, instance}
    for instance, qradar_token in QRADAR_TOKENS.items():
        assert holders[qradar_token] == {instance}
    # Profile tokens are mounted only into the gateway. Outside Compose, the executor's token is
    # apart from the agents': the workers get secrets/agents/, the executor secrets/executor/.
    files = {name: secret["file"] for name, secret in compose["secrets"].items()}
    for profile, entry in connector["profiles"].items():
        executor = any(tool.get("caller") == "action-executor" for tool in entry["tools"])
        directory = "executor" if executor else "agents"
        assert holders[f"gateway-token-{profile}"] == {GATEWAY}
        assert files[f"gateway-token-{profile}"] == f"./secrets/{directory}/gateway-token-{profile}"
    assert files[EXECUTOR_MOUNT] == f"./secrets/executor/{EXECUTOR_MOUNT}"


# --- criterion 6: the lab override -----------------------------------------------------------


def test_the_lab_override_puts_only_the_mcp_instances_on_qradar_vmnet() -> None:
    lab = load(LAB_FILE)

    assert lab["networks"] == {"qradar-vmnet": {"external": True}}
    assert lab["services"] == {name: {"networks": {"qradar-vmnet": {}}} for name in MCP_INSTANCES}


def docker_compose() -> str | None:
    docker = shutil.which("docker")
    if docker is None:
        return None
    version = subprocess.run([docker, "compose", "version"], capture_output=True, check=False)  # noqa: S603
    return docker if version.returncode == 0 else None


def test_the_merged_lab_stack_keeps_every_other_service_off_the_lab_segment(
    tmp_path: Path,
) -> None:
    docker = docker_compose()
    if docker is None:
        pytest.skip("docker compose is not installed")
    empty = tmp_path / "empty.env"
    empty.touch()
    names = [
        line.partition("=")[0]
        for line in (COMPOSE_DIR / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    environment = {
        k: v for k, v in os.environ.items() if k not in names and k != "COMPOSE_PROFILES"
    }

    rendered = subprocess.run(  # noqa: S603
        [
            docker,
            "compose",
            "--env-file",
            str(empty),
            "-f",
            str(DEV_FILE),
            "-f",
            str(LAB_FILE),
            "--profile",
            "qradar",
            "config",
            "--format",
            "json",
        ],
        env=environment | {name: "placeholder" for name in names},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert rendered.returncode == 0, rendered.stderr
    services = json.loads(rendered.stdout)["services"]
    on_lab = {name for name, s in services.items() if "qradar-vmnet" in (s.get("networks") or {})}
    assert on_lab == set(MCP_INSTANCES)
    for name in MCP_INSTANCES:
        assert set(services[name]["networks"]) == {"mcp", "qradar-egress", "qradar-vmnet"}
        assert services[name]["networks"]["mcp"]["aliases"] == [f"{name}.mcp"]
    assert set(services[GATEWAY]["networks"]) == {"default", "mcp"}


# --- deploy/compose/make_secrets.py ----------------------------------------------------------


@pytest.fixture(scope="module")
def make_secrets() -> ModuleType:
    """deploy/compose/make_secrets.py, loaded by file path."""
    path = COMPOSE_DIR / "make_secrets.py"
    spec = importlib.util.spec_from_file_location("ais0c_make_secrets", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def narrow_umask() -> Iterator[None]:
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def compose_secret_files() -> set[str]:
    return {secret["file"].removeprefix("./secrets/") for secret in dev()["secrets"].values()}


def written_files(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): path.read_text(encoding="utf-8").strip()
        for path in directory.rglob("*")
        if path.is_file()
    }


def test_make_secrets_writes_every_file_the_stack_mounts(
    make_secrets: ModuleType, tmp_path: Path, narrow_umask: None
) -> None:
    report = make_secrets.make_secrets(tmp_path, make_secrets.plan(manifest()), SYNTHETIC_QRADAR)

    assert set(report.created) == set(written_files(tmp_path)) == compose_secret_files()
    assert report.missing == []
    # Readable by the containers' users through the bind mounts; the directories are private.
    for path in [tmp_path, *tmp_path.rglob("*")]:
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o644), path


def test_make_secrets_makes_unique_random_tokens(make_secrets: ModuleType, tmp_path: Path) -> None:
    make_secrets.make_secrets(tmp_path, make_secrets.plan(manifest()), SYNTHETIC_QRADAR)

    random = {
        name: token
        for name, token in written_files(tmp_path).items()
        if not name.startswith("qradar/")
    }
    assert len(random) == 9
    assert len(set(random.values())) == len(random)
    assert all(len(token) == 64 for token in random.values())
    assert (
        written_files(tmp_path)["qradar/qradar-token-read"]
        == SYNTHETIC_QRADAR["AIS0C_QRADAR_READ_TOKEN"]
    )


def test_make_secrets_keeps_existing_files_and_names_missing_qradar_tokens(
    make_secrets: ModuleType, tmp_path: Path
) -> None:
    plan = make_secrets.plan(manifest())

    first = make_secrets.make_secrets(tmp_path, plan, {})
    before = written_files(tmp_path)
    second = make_secrets.make_secrets(tmp_path, plan, SYNTHETIC_QRADAR)

    assert first.missing == [
        ("qradar/qradar-token-note", "AIS0C_QRADAR_NOTE_TOKEN"),
        ("qradar/qradar-token-read", "AIS0C_QRADAR_READ_TOKEN"),
    ]
    assert sorted(second.created) == ["qradar/qradar-token-note", "qradar/qradar-token-read"]
    after = written_files(tmp_path)
    assert {name: after[name] for name in before} == before


def test_make_secrets_refuses_a_qradar_token_with_spaces(
    make_secrets: ModuleType, tmp_path: Path
) -> None:
    environ = SYNTHETIC_QRADAR | {"AIS0C_QRADAR_NOTE_TOKEN": "two words"}

    with pytest.raises(make_secrets.SecretsError, match="AIS0C_QRADAR_NOTE_TOKEN"):
        make_secrets.make_secrets(tmp_path, make_secrets.plan(manifest()), environ)


def test_make_secrets_never_prints_a_token(
    make_secrets: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name, value in SYNTHETIC_QRADAR.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(make_secrets, "label_for_containers", lambda directory: None)

    assert make_secrets.main(["--directory", str(tmp_path)]) == 0

    printed = capsys.readouterr()
    for token in written_files(tmp_path).values():
        assert token not in printed.out + printed.err


def test_the_gateway_starts_with_the_made_secrets(
    make_secrets: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Compose mounts the gateway's files flat into /run/secrets, with the compose environment.
    made, run_secrets = tmp_path / "made", tmp_path / "run-secrets"
    make_secrets.make_secrets(made, make_secrets.plan(manifest()), SYNTHETIC_QRADAR)
    gateway = dev()["services"][GATEWAY]
    run_secrets.mkdir()
    for secret in gateway["secrets"]:
        file = dev()["secrets"][secret]["file"].removeprefix("./secrets/")
        shutil.copy(made / file, run_secrets / secret)
    environment = {
        name: value
        for name, value in gateway["environment"].items()
        if name.startswith("AIS0C_GATEWAY_UPSTREAM_URL_")
    }
    monkeypatch.setenv("AIS0C_DATABASE_URL", "postgresql+psycopg://ais0c:x@127.0.0.1:1/ais0c")

    service = build_service(
        Settings.from_env(
            environment
            | {
                "AIS0C_GATEWAY_CONFIG_DIR": str(CONFIG_DIR),
                "AIS0C_GATEWAY_SECRETS_DIR": str(run_secrets),
            }
        ),
        configure_logs=False,
    )

    assert set(service.gateway.registry.profiles) == set(manifest()["profiles"])
    assert set(service.gateway.upstreams) == set(MCP_INSTANCES)
