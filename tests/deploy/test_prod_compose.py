"""Static checks of the prod shadow stack in deploy/compose/ (T-077 acceptance criteria 1 to 8).

They reuse the helpers of test_compose_file.py, which checks the dev stack: the same rules, run
against docker-compose.prod.yaml. Each rule also runs against a broken input to show that it
catches the problem. Fake values are placeholders, RFC 5737 addresses and example.com only.
"""

import copy
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_compose_file import (
    COMPOSE_DIR,
    INTERPOLATION,
    bind_mounts,
    environment,
    executable,
    has_docker_compose,
    image_problems,
    image_references,
    one_shot_jobs,
    resolve_default,
    secret_files,
    secret_problems,
)

PROD_FILE = COMPOSE_DIR / "docker-compose.prod.yaml"
PROD_EXAMPLE = COMPOSE_DIR / ".env.prod.example"
DEV_FILE = COMPOSE_DIR / "docker-compose.dev.yaml"
OWN_TAG = "${AIS0C_VERSION:?"
WORKERS = ("case-worker", "batch-worker", "executor-worker")
MIGRATING = (*WORKERS, "api")
# The services we build and run ourselves (T-076 images, the qradar-mcp fork, the gateway).
OWN_SERVICES = (
    "mcp-gateway",
    "qradar-mcp-read",
    "qradar-mcp-note",
    "migrate",
    *MIGRATING,
    "ui",
)
SECRETS_SUBDIRECTORIES = {"agents", "executor", "mcp", "qradar", "api", "ui"}
SECRETS_PREFIX = "${AIS0C_SECRETS_DIR:?"
# Variables whose fake value must be an address.
FAKE_VALUES = {"AIS0C_UI_BIND": "127.0.0.1", "AIS0C_SECRETS_DIR": "/srv/ais0c-secrets"}


def load_prod() -> dict[str, Any]:
    return yaml.safe_load(PROD_FILE.read_text(encoding="utf-8"))


def prod_example() -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in PROD_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            name, sep, value = line.partition("=")
            assert sep, f"not a NAME=value line: {line!r}"
            entries[name.strip()] = value
    return entries


# --- images (criterion 1) -----------------------------------------------------------------


def prod_image_problems(compose: dict[str, Any]) -> dict[str, list[str]]:
    """Third-party images need tag and digest; our own need ${AIS0C_VERSION:?}, `pull_policy:
    never` and no `build`. The fork is our own image, pinned by its commit tag."""
    problems: dict[str, list[str]] = {}
    for where, reference in image_references(compose).items():
        service = compose["services"][where.partition(":")[0]]
        found: list[str]
        if reference.rpartition(":")[0].startswith(("ais0c-", "qradar-mcp-fork")):
            found = []
            if reference.startswith("ais0c-"):
                if not reference.endswith(":" + OWN_TAG + reference.split(OWN_TAG, 1)[-1]):
                    found.append("does not use the release tag")
                if OWN_TAG not in reference:
                    found.append("does not use ${AIS0C_VERSION:?}")
            else:
                found.extend(image_problems(reference, local=True))
            if service.get("pull_policy") != "never":
                found.append("is not pull_policy: never")
            if "build" in service:
                found.append("has a build section")
        else:
            found = image_problems(reference)
        if found:
            problems[where] = found
    return problems


def test_prod_images_are_pinned() -> None:
    compose = load_prod()
    references = image_references(compose)

    assert references["ui"].startswith("ais0c-ui:" + OWN_TAG)
    assert references["otel-collector:/probe"].startswith("docker.io/library/busybox:")
    assert prod_image_problems(compose) == {}


def test_third_party_images_equal_the_dev_images() -> None:
    dev = image_references(yaml.safe_load(DEV_FILE.read_text(encoding="utf-8")))
    prod = image_references(load_prod())

    third_party = {
        where: reference
        for where, reference in prod.items()
        if not reference.startswith(("ais0c-", "qradar-mcp-fork:"))
    }
    assert third_party
    for where, reference in third_party.items():
        assert reference in dev.values(), f"{where}: {reference} is not a dev image"
    assert prod["qradar-mcp-read"] == dev["qradar-mcp-read"]


@pytest.mark.parametrize(
    ("service", "change"),
    [
        ("case-worker", {"image": "ais0c-platform:latest"}),
        ("case-worker", {"image": "ais0c-platform:1.0"}),
        ("api", {"pull_policy": "build"}),
        ("api", {"build": {"context": "../.."}}),
        ("temporal", {"image": "docker.io/temporalio/server:1.31.3"}),
        ("litellm", {"image": "ghcr.io/berriai/litellm:latest@sha256:" + "0" * 64}),
    ],
    ids=["latest", "fixed-version", "pull-build", "build-section", "no-digest", "third-latest"],
)
def test_own_image_with_latest_is_reported(service: str, change: dict[str, Any]) -> None:
    compose = copy.deepcopy(load_prod())
    compose["services"][service].update(change)

    assert service in prod_image_problems(compose)


# --- fixed settings (criterion 2) ---------------------------------------------------------


def fixed_setting_problems(compose: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    expected = {
        "AIS0C_SKILLS_MODE": "prod",
        "AIS0C_MODEL_REGISTRY": "config/models/registry.prod.yaml",
    }
    for name in WORKERS:
        env = environment(compose["services"][name])
        problems.extend(
            f"{name}: {key} is {env.get(key)!r}, not {value!r}"
            for key, value in expected.items()
            if env.get(key) != value
        )
    return problems


def test_prod_fixes_skills_mode_and_registry() -> None:
    compose = load_prod()

    assert fixed_setting_problems(compose) == []
    fixed = {
        "LITELLM_BASE_URL": "http://litellm:4000",
        "AIS0C_GATEWAY_URL": "http://mcp-gateway:8080",
        "TEMPORAL_ADDRESS": "temporal:7233",
    }
    for name in WORKERS:
        env = environment(compose["services"][name])
        assert {key: env[key] for key in fixed if key in env} == {
            key: value for key, value in fixed.items() if key in env
        }, name
        assert env["AIS0C_GATEWAY_URL"] == fixed["AIS0C_GATEWAY_URL"], name
        assert env["TEMPORAL_ADDRESS"] == fixed["TEMPORAL_ADDRESS"], name
    assert (
        environment(compose["services"]["case-worker"])["LITELLM_BASE_URL"]
        == fixed["LITELLM_BASE_URL"]
    )


def test_dev_skills_mode_in_prod_is_reported() -> None:
    compose = copy.deepcopy(load_prod())
    compose["services"]["case-worker"]["environment"]["AIS0C_SKILLS_MODE"] = "dev"
    compose["services"]["batch-worker"]["environment"]["AIS0C_MODEL_REGISTRY"] = (
        "config/models/registry.dev.yaml"
    )

    assert fixed_setting_problems(compose) == [
        "case-worker: AIS0C_SKILLS_MODE is 'dev', not 'prod'",
        "batch-worker: AIS0C_MODEL_REGISTRY is 'config/models/registry.dev.yaml', "
        "not 'config/models/registry.prod.yaml'",
    ]


def test_fixed_settings_do_not_come_from_the_environment() -> None:
    for name in WORKERS:
        for key in ("AIS0C_SKILLS_MODE", "AIS0C_MODEL_REGISTRY", "AIS0C_GATEWAY_URL"):
            assert "$" not in str(environment(load_prod()["services"][name])[key])


# --- ports (criterion 3) ------------------------------------------------------------------


def published_port_problems(compose: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for name, service in compose["services"].items():
        for port in service.get("ports", []):
            if not isinstance(port, str):
                problems.append(f"{name}: use the short port syntax")
            elif name == "ui" and port.endswith(":8443:8443"):
                continue
            elif name == "temporal-ui" and port.startswith("127.0.0.1:"):
                continue
            else:
                problems.append(f"{name}: publishes {port}")
    return problems


def test_only_the_ui_port_is_published() -> None:
    compose = load_prod()

    assert published_port_problems(compose) == []
    published = {name for name, service in compose["services"].items() if service.get("ports")}
    assert published == {"ui", "temporal-ui"}
    assert compose["services"]["ui"]["ports"] == ["${AIS0C_UI_BIND:-0.0.0.0}:8443:8443"]
    assert compose["services"]["temporal-ui"]["ports"] == ["127.0.0.1:8233:8080"]


@pytest.mark.parametrize(
    ("service", "port"),
    [
        ("api", "0.0.0.0:8000:8000"),
        ("postgres", "127.0.0.1:5432:5432"),
        ("temporal-ui", "8233:8080"),
    ],
    ids=["api", "postgres", "temporal-ui-on-all-interfaces"],
)
def test_published_api_port_is_reported(service: str, port: str) -> None:
    compose = copy.deepcopy(load_prod())
    compose["services"][service]["ports"] = [port]

    assert published_port_problems(compose) != []


# --- secrets (criteria 4 and 5) -----------------------------------------------------------


def test_prod_secrets_come_only_from_files_or_the_environment() -> None:
    compose = load_prod()

    assert secret_problems(compose) == []
    assert compose["secrets"]
    for name, secret in compose["secrets"].items():
        file = secret["file"]
        assert file.startswith(SECRETS_PREFIX), name
        relative = file.partition("}/")[2]
        assert relative.partition("/")[0] in SECRETS_SUBDIRECTORIES, name
        # Inside the container the name stays the dev name; ui/tls.crt is mounted as ui-tls.crt.
        assert relative.rpartition("/")[2] == name.replace("ui-tls", "tls"), name


def test_prod_secret_layout_is_the_one_make_secrets_writes() -> None:
    spec = yaml.safe_load((COMPOSE_DIR.parents[1] / "config/connectors/qradar.yaml").read_text())
    expected = {
        f"{'executor' if tool.get('caller') == 'action-executor' else 'agents'}"
        f"/gateway-token-{name}"
        for name, profile in spec["profiles"].items()
        for tool in profile["tools"][:1]
    }
    declared = {secret["file"].partition("}/")[2] for secret in load_prod()["secrets"].values()}

    assert expected <= declared


def test_every_mounted_secret_is_declared_and_used() -> None:
    compose = load_prod()
    mounted = {
        item if isinstance(item, str) else item["source"]
        for service in compose["services"].values()
        for item in service.get("secrets") or []
    }

    assert mounted == set(compose["secrets"])


def worker_secret_names(compose: dict[str, Any], name: str) -> set[str]:
    return {Path(path).name for path in secret_files(compose["services"][name])}


def test_case_worker_gets_no_note_token() -> None:
    compose = load_prod()
    held = worker_secret_names(compose, "case-worker")

    assert held == {
        "gateway-token-qradar-triage-read",
        "gateway-token-qradar-investigate-read",
        "gateway-token-qradar-verify-read",
    }
    for name, service in compose["services"].items():
        if "gateway-token-qradar-note-write" in worker_secret_names(compose, name):
            assert name in {"executor-worker", "mcp-gateway"}, service["image"]


def test_note_token_in_the_case_worker_is_reported() -> None:
    compose = copy.deepcopy(load_prod())
    compose["services"]["case-worker"]["secrets"].append("gateway-token-qradar-note-write")

    assert "gateway-token-qradar-note-write" in worker_secret_names(compose, "case-worker")


def test_executor_gets_only_the_note_token() -> None:
    compose = load_prod()

    assert worker_secret_names(compose, "executor-worker") == {"gateway-token-qradar-note-write"}
    for name in ("batch-worker", "api", "ui", "migrate"):
        assert "gateway-token-qradar-note-write" not in worker_secret_names(compose, name)
    assert worker_secret_names(compose, "batch-worker") == {"gateway-token-qradar-inventory-read"}
    assert not any(
        "qradar-token" in secret for secret in worker_secret_names(compose, "case-worker")
    )


# --- dependencies (criterion 6) -----------------------------------------------------------


def migrate_problems(compose: dict[str, Any]) -> list[str]:
    problems = [
        f"{name} does not wait for migrate"
        for name in MIGRATING
        if (compose["services"][name].get("depends_on") or {}).get("migrate", {}).get("condition")
        != "service_completed_successfully"
    ]
    if compose["services"]["migrate"].get("restart") != "no":
        problems.append("migrate restarts")
    return problems


def test_workers_wait_for_migrate() -> None:
    compose = load_prod()

    assert migrate_problems(compose) == []
    assert compose["services"]["migrate"]["command"] == ["ais0c_worker", "migrate"]
    assert "migrate" in one_shot_jobs(compose)
    for name in WORKERS:
        depends = compose["services"][name]["depends_on"]
        assert depends["mcp-gateway"]["condition"] == "service_healthy", name
        assert depends["temporal-admin-tools"]["condition"] == "service_healthy", name


def test_a_service_that_skips_migrate_is_reported() -> None:
    compose = copy.deepcopy(load_prod())
    del compose["services"]["api"]["depends_on"]["migrate"]
    compose["services"]["migrate"]["restart"] = "unless-stopped"

    assert migrate_problems(compose) == ["api does not wait for migrate", "migrate restarts"]


def test_every_long_running_prod_service_has_a_healthcheck() -> None:
    compose = load_prod()
    jobs = one_shot_jobs(compose) | {"migrate"}

    for name, service in compose["services"].items():
        if name in jobs:
            assert service.get("restart") == "no", name
        else:
            assert (service.get("healthcheck") or {}).get("test"), name
        for dependency, condition in (service.get("depends_on") or {}).items():
            assert condition["condition"] in {"service_healthy", "service_completed_successfully"}
            assert dependency in compose["services"], f"{name} -> {dependency}"


# --- hardening (criterion 7) --------------------------------------------------------------


def hardening_problems(compose: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for name in OWN_SERVICES:
        service = compose["services"][name]
        if service.get("read_only") is not True:
            problems.append(f"{name}: not read_only")
        if service.get("cap_drop") != ["ALL"]:
            problems.append(f"{name}: cap_drop is not [ALL]")
        if "no-new-privileges:true" not in (service.get("security_opt") or []):
            problems.append(f"{name}: no no-new-privileges")
        if "tmpfs" in service and "/tmp" not in service["tmpfs"]:  # noqa: S108
            problems.append(f"{name}: tmpfs lacks /tmp")
        if name != "migrate" and service.get("restart") != "unless-stopped":
            problems.append(f"{name}: restart is not unless-stopped")
    return problems


def test_hardening_on_every_own_service() -> None:
    assert hardening_problems(load_prod()) == []


def test_a_service_without_hardening_is_reported() -> None:
    compose = copy.deepcopy(load_prod())
    compose["services"]["api"]["read_only"] = False
    del compose["services"]["ui"]["cap_drop"]
    compose["services"]["migrate"]["security_opt"] = []

    assert sorted(hardening_problems(compose)) == [
        "api: not read_only",
        "migrate: no no-new-privileges",
        "ui: cap_drop is not [ALL]",
    ]


# --- shadow and isolation -----------------------------------------------------------------


def test_prod_has_no_profiles_no_mail_catcher_and_no_dev_models() -> None:
    compose = load_prod()
    text = PROD_FILE.read_text(encoding="utf-8")

    assert "mailpit" not in compose["services"]
    assert all("profiles" not in service for service in compose["services"].values())
    assert "litellm.prod.yaml" in text
    assert "litellm.dev" not in text
    assert "OPENROUTER" not in text


def test_nothing_opens_writes_in_prod() -> None:
    compose = load_prod()
    code = "\n".join(
        line
        for line in PROD_FILE.read_text("utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ).lower()

    assert "writes_enabled" not in code
    assert "kill_switch" not in code
    for name, service in compose["services"].items():
        assert not [key for key in environment(service) if "WRITE" in key], name


def test_the_internal_network_has_no_route_out_and_the_egress_net_is_a_bridge() -> None:
    networks = load_prod()["networks"]

    assert networks["mcp"] == {"internal": True}
    assert networks["qradar-egress"] in ({}, None)
    for name in ("qradar-mcp-read", "qradar-mcp-note"):
        assert set(load_prod()["services"][name]["networks"]) == {"mcp", "qradar-egress"}


def test_bind_mounts_are_read_only_and_exist() -> None:
    mounts = bind_mounts(load_prod())

    assert mounts
    for name, source, target, options in mounts:
        assert (COMPOSE_DIR / resolve_default(source)).exists(), f"{name}: {source} is missing"
        assert "ro" in options, f"{name}: {target} is writable"
        assert "z" in options, f"{name}: {target} needs the SELinux label z"


def test_litellm_mounts_the_prod_config_and_publishes_no_port() -> None:
    litellm = load_prod()["services"]["litellm"]

    assert litellm["volumes"] == [
        "../../config/litellm/litellm.prod.yaml:/etc/litellm/config.yaml:ro,z"
    ]
    assert "ports" not in litellm


def test_qradar_console_is_required() -> None:
    compose = load_prod()

    for name in ("qradar-mcp-read", "qradar-mcp-note"):
        value = environment(compose["services"][name])["QRADAR_CONSOLE_FQDN"]
        assert value is not None
        assert value.startswith("${QRADAR_CONSOLE_FQDN:?")


# --- the example file and docker compose (criterion 8) ------------------------------------


def test_env_example_lists_every_variable() -> None:
    used = {match["name"] for match in INTERPOLATION.finditer(PROD_FILE.read_text("utf-8"))}

    assert used == set(prod_example())


def test_a_variable_missing_from_the_example_is_reported() -> None:
    used = {match["name"] for match in INTERPOLATION.finditer("a: ${NEW_ONE:?x}")}

    assert used - set(prod_example()) == {"NEW_ONE"}


def test_prod_env_example_has_only_empty_values_and_a_turkish_line_above_each() -> None:
    lines = PROD_EXAMPLE.read_text(encoding="utf-8").splitlines()
    entries = prod_example()

    assert entries
    assert {name: value for name, value in entries.items() if value.strip()} == {}
    for index, line in enumerate(lines):
        if line and not line.startswith("#"):
            assert lines[index - 1].startswith("# "), line
    for name in (
        "AIS0C_VERSION",
        "AIS0C_SECRETS_DIR",
        "POSTGRES_PASSWORD",
        "AIS0C_DB_PASSWORD",
        "TEMPORAL_DB_PASSWORD",
        "LITELLM_MASTER_KEY",
        "QRADAR_CONSOLE_FQDN",
        "QRADAR_VERIFY_SSL",
        "AIS0C_SMTP_HOST",
        "AIS0C_SMTP_PORT",
        "AIS0C_SMTP_TLS",
        "AIS0C_SMTP_FROM",
        "AIS0C_CASE_URL_BASE",
        "AIS0C_QRADAR_OFFENSE_URL_TEMPLATE",
        "AIS0C_API_AUTH",
        "AIS0C_UI_BIND",
        "AIS0C_ALARM_SYSLOG_HOST",
        "AIS0C_ALARM_SYSLOG_PORT",
        "AIS0C_ALARM_SYSLOG_PROTOCOL",
    ):
        assert name in entries


def test_every_litellm_variable_is_in_the_example() -> None:
    config = (COMPOSE_DIR.parents[1] / "config/litellm/litellm.prod.yaml").read_text("utf-8")
    variables = set(re.findall(r"os\.environ/(VLLM_[A-Z0-9_]+)", config))

    assert variables
    assert variables <= set(prod_example())


needs_compose = pytest.mark.skipif(
    not has_docker_compose(), reason="docker compose is not installed"
)


def prod_config(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run `docker compose config` on the prod file with only the given variables."""
    base = {key: value for key, value in os.environ.items() if key not in prod_example()}
    return subprocess.run(  # noqa: S603
        [
            executable("docker"),
            "compose",
            "--env-file",
            os.devnull,
            "-f",
            str(PROD_FILE),
            "config",
            "--quiet",
        ],
        env=base | env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def fake_values() -> dict[str, str]:
    return {name: FAKE_VALUES.get(name, "placeholder") for name in prod_example()}


@needs_compose
def test_prod_compose_config_is_valid() -> None:
    env = fake_values() | {
        "QRADAR_CONSOLE_FQDN": "qradar.example.com",
        "VLLM_QWEN_122B_API_BASE": "http://192.0.2.10:8000/v1",
        "VLLM_DEEPSEEK_V4_FLASH_API_BASE": "http://198.51.100.20:8000/v1",
        "AIS0C_ALARM_SYSLOG_HOST": "203.0.113.5",
    }

    result = prod_config(env)

    assert result.returncode == 0, result.stderr


@needs_compose
@pytest.mark.parametrize(
    "variable",
    [
        "AIS0C_VERSION",
        "AIS0C_SECRETS_DIR",
        "POSTGRES_PASSWORD",
        "AIS0C_DB_PASSWORD",
        "TEMPORAL_DB_PASSWORD",
        "LITELLM_MASTER_KEY",
        "QRADAR_CONSOLE_FQDN",
        "AIS0C_CASE_URL_BASE",
        "AIS0C_API_AUTH",
        "AIS0C_SMTP_HOST",
        "AIS0C_SMTP_FROM",
        "VLLM_QWEN_122B_API_BASE",
        "VLLM_DEEPSEEK_V4_FLASH_API_BASE",
    ],
)
def test_prod_compose_stops_without_a_required_value(variable: str) -> None:
    env = {name: value for name, value in fake_values().items() if name != variable}

    result = prod_config(env)

    assert result.returncode != 0
    assert variable in result.stderr


def test_prod_compose_secret_check_catches_a_literal() -> None:
    compose = copy.deepcopy(load_prod())
    compose["services"]["litellm"]["environment"]["LITELLM_MASTER_KEY"] = "sk-literal"

    assert secret_problems(compose) == [
        "litellm: LITELLM_MASTER_KEY is not taken from the environment"
    ]
