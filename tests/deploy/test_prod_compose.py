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
GATE = "preflight"
MIGRATING = (*WORKERS, "api")
# The services we build and run ourselves (T-076 images, the qradar-mcp fork, the gateway).
OWN_SERVICES = (
    "mcp-gateway",
    "litellm",
    "preflight",
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
    never` and no `build`. The fork (D-46) is one of our own images."""
    problems: dict[str, list[str]] = {}
    for where, reference in image_references(compose).items():
        service = compose["services"][where.partition(":")[0]]
        found: list[str]
        if reference.rpartition(":")[0].startswith(("ais0c-", "qradar-mcp-fork")):
            found = []
            if not reference.endswith(":" + OWN_TAG + reference.split(OWN_TAG, 1)[-1]):
                found.append("does not use the release tag")
            if OWN_TAG not in reference:
                found.append("does not use ${AIS0C_VERSION:?}")
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
    # The fork is one of our own images (D-46): `:dev` built here, `:<release>` in prod.
    assert dev["qradar-mcp-read"] == "qradar-mcp-fork:dev"
    assert prod["qradar-mcp-read"].startswith("qradar-mcp-fork:" + OWN_TAG)


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

    # The note token, and the relay password (read only when AIS0C_SMTP_USERNAME is set).
    assert worker_secret_names(compose, "executor-worker") == {
        "gateway-token-qradar-note-write",
        "smtp-password",
    }
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
    for name, service in compose["services"].items():
        if service.get("privileged"):
            problems.append(f"{name}: privileged")
        for key in ("network_mode", "pid", "ipc", "userns_mode"):
            if service.get(key) == "host":
                problems.append(f"{name}: {key} is host")
        for volume in service.get("volumes") or []:
            text = volume if isinstance(volume, str) else str(volume.get("source", ""))
            if "docker.sock" in text:
                problems.append(f"{name}: mounts the Docker socket")
    for name in OWN_SERVICES:
        service = compose["services"][name]
        if service.get("read_only") is not True:
            problems.append(f"{name}: not read_only")
        if service.get("cap_drop") != ["ALL"]:
            problems.append(f"{name}: cap_drop is not [ALL]")
        if "no-new-privileges:true" not in (service.get("security_opt") or []):
            problems.append(f"{name}: no no-new-privileges")
        if "/tmp" not in (service.get("tmpfs") or []):  # noqa: S108
            problems.append(f"{name}: tmpfs lacks /tmp")
        if name not in {"migrate", GATE} and service.get("restart") != "unless-stopped":
            problems.append(f"{name}: restart is not unless-stopped")
    return problems


def test_hardening_on_every_own_service() -> None:
    assert hardening_problems(load_prod()) == []


def test_a_service_without_hardening_is_reported() -> None:
    compose = copy.deepcopy(load_prod())
    compose["services"]["api"]["read_only"] = False
    del compose["services"]["ui"]["cap_drop"]
    compose["services"]["migrate"]["security_opt"] = []

    del compose["services"]["case-worker"]["tmpfs"]
    assert sorted(hardening_problems(compose)) == [
        "api: not read_only",
        "case-worker: tmpfs lacks /tmp",
        "migrate: no no-new-privileges",
        "ui: cap_drop is not [ALL]",
    ]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"privileged": True}, "api: privileged"),
        ({"network_mode": "host"}, "api: network_mode is host"),
        ({"pid": "host"}, "api: pid is host"),
        (
            {"volumes": ["/var/run/docker.sock:/var/run/docker.sock"]},
            "api: mounts the Docker socket",
        ),
        ({"tmpfs": []}, "api: tmpfs lacks /tmp"),
    ],
    ids=["privileged", "host-network", "host-pid", "docker-socket", "no-tmpfs"],
)
def test_a_dangerous_or_unhardened_service_setting_is_reported(
    change: dict[str, Any], expected: str
) -> None:
    compose = copy.deepcopy(load_prod())
    compose["services"]["api"].update(change)

    assert expected in hardening_problems(compose)


# --- preflight gate and credentials -------------------------------------------------------


def gate_problems(compose: dict[str, Any]) -> list[str]:
    problems = [
        f"{name} does not wait for preflight"
        for name in WORKERS
        if (compose["services"][name].get("depends_on") or {}).get(GATE, {}).get("condition")
        != "service_completed_successfully"
    ]
    gate = compose["services"][GATE]
    if gate.get("restart") != "no":
        problems.append("preflight restarts")
    if gate.get("command") != ["ais0c_worker", "preflight"]:
        problems.append("preflight runs another command")
    depends = gate.get("depends_on") or {}
    expected = {
        "migrate": "service_completed_successfully",
        "temporal-admin-tools": "service_healthy",
        "mcp-gateway": "service_healthy",
        "litellm": "service_healthy",
    }
    problems.extend(
        f"preflight does not wait for {dependency}"
        for dependency, condition in expected.items()
        if depends.get(dependency, {}).get("condition") != condition
    )
    return problems


def test_writers_wait_for_preflight() -> None:
    compose = load_prod()

    assert gate_problems(compose) == []
    assert GATE in one_shot_jobs(compose)
    # The API and the UI start without it: admins must be able to see the flag.
    assert GATE not in (compose["services"]["api"].get("depends_on") or {})
    assert GATE not in (compose["services"]["ui"].get("depends_on") or {})
    # Nothing but the three workers is held back by it.
    waiting = {
        name
        for name, service in compose["services"].items()
        if GATE in (service.get("depends_on") or {})
    }
    assert waiting == set(WORKERS)


def test_a_worker_that_skips_preflight_is_reported() -> None:
    compose = copy.deepcopy(load_prod())
    del compose["services"]["executor-worker"]["depends_on"][GATE]
    compose["services"][GATE]["restart"] = "unless-stopped"
    compose["services"][GATE]["depends_on"].pop("litellm")

    assert gate_problems(compose) == [
        "executor-worker does not wait for preflight",
        "preflight restarts",
        "preflight does not wait for litellm",
    ]


def vllm_holders(compose: dict[str, Any]) -> set[str]:
    return {
        name
        for name, service in compose["services"].items()
        if any(key.startswith("VLLM_") for key in environment(service))
    }


def test_only_preflight_and_litellm_get_vllm_credentials() -> None:
    compose = load_prod()

    assert vllm_holders(compose) == {GATE, "litellm"}
    # The preflight reads the LiteLLM config from the litellm image, not from the repository.
    volumes = compose["services"][GATE]["volumes"]
    assert [(v["type"], v["target"]) for v in volumes] == [("image", "/app/config/litellm")]
    assert volumes[0]["source"].startswith("ais0c-litellm:" + OWN_TAG)
    for name in WORKERS:
        assert "volumes" not in compose["services"][name]


def test_a_worker_with_vllm_credentials_is_reported() -> None:
    compose = copy.deepcopy(load_prod())
    compose["services"]["case-worker"]["environment"]["VLLM_QWEN_122B_API_KEY"] = "${X:-}"

    assert vllm_holders(compose) == {GATE, "litellm", "case-worker"}


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


def network_members(compose: dict[str, Any]) -> dict[str, set[str]]:
    members: dict[str, set[str]] = {}
    for name, service in compose["services"].items():
        networks = service.get("networks")
        # A service without `networks` is on the default network only.
        for network in networks if networks is not None else ["default"]:
            members.setdefault(network, set()).add(name)
    return members


def network_problems(compose: dict[str, Any]) -> list[str]:
    expected = {
        "mcp": {"mcp-gateway", "qradar-mcp-read", "qradar-mcp-note"},
        "qradar-egress": {"qradar-mcp-read", "qradar-mcp-note"},
    }
    members = network_members(compose)
    problems = [
        f"{network}: members are {sorted(members.get(network, set()))}, not {sorted(allowed)}"
        for network, allowed in expected.items()
        if members.get(network, set()) != allowed
    ]
    if compose["networks"].get("mcp") != {"internal": True}:
        problems.append("mcp is not internal")
    unknown = set(members) - {"default", *expected}
    problems.extend(f"unknown network {network}" for network in sorted(unknown))
    return problems


def test_the_mcp_and_egress_networks_have_exactly_their_members() -> None:
    compose = load_prod()

    assert network_problems(compose) == []
    assert compose["networks"]["qradar-egress"] in ({}, None)
    assert "network_mode" not in str(compose["services"])


@pytest.mark.parametrize(
    ("service", "network"),
    [
        ("case-worker", "mcp"),
        ("api", "qradar-egress"),
        ("mcp-gateway", "qradar-egress"),
        ("litellm", "mcp"),
    ],
)
def test_a_service_on_a_restricted_network_is_reported(service: str, network: str) -> None:
    compose = copy.deepcopy(load_prod())
    service_networks = compose["services"][service].get("networks") or ["default"]
    compose["services"][service]["networks"] = [*service_networks, network]

    assert network_problems(compose) != []


def test_a_missing_member_and_an_open_mcp_network_are_reported() -> None:
    compose = copy.deepcopy(load_prod())
    compose["services"]["qradar-mcp-note"]["networks"] = ["mcp"]
    compose["networks"]["mcp"] = {}

    assert len(network_problems(compose)) == 2


# Helper files that sit next to the compose file and configure third-party images. The release
# itself (policies, telemetry, LiteLLM config...) is inside our images.
SIBLING_MOUNTS = {
    "./postgres/init",
    "./temporal/setup-schema.sh",
    "./temporal/dynamicconfig",
    "./temporal/create-namespace.sh",
    "./otel-collector/config.yaml",
}


def repository_mount_problems(compose: dict[str, Any]) -> list[str]:
    """Bind mounts that are writable, lack the SELinux label, reach outside deploy/compose or are
    not one of the helper files; and any volume of the Docker socket or of a host path."""
    problems: list[str] = []
    for name, source, target, options in bind_mounts(compose):
        if source not in SIBLING_MOUNTS:
            problems.append(f"{name}: bind mount {source} is not a sibling helper file")
        if "ro" not in options or "z" not in options:
            problems.append(f"{name}: {target} needs ro and z")
        if not (COMPOSE_DIR / resolve_default(source)).exists():
            problems.append(f"{name}: {source} is missing")
    return problems


def test_prod_has_no_repository_bind_mounts() -> None:
    compose = load_prod()

    assert bind_mounts(compose)
    assert repository_mount_problems(compose) == []
    for name in (*OWN_SERVICES, "litellm", GATE):
        volumes = compose["services"][name].get("volumes") or []
        assert not [v for v in volumes if isinstance(v, str)], name
    assert "config/" not in "".join(m[1] for m in bind_mounts(compose))


@pytest.mark.parametrize(
    ("service", "volume"),
    [
        ("case-worker", "../../config/policies:/app/config/policies:ro,z"),
        ("mcp-gateway", "../../config/connectors:/etc/ais0c/connectors:ro,z"),
        ("litellm", "../../config/litellm/litellm.prod.yaml:/etc/litellm/config.yaml:ro,z"),
        ("postgres", "./postgres/init:/docker-entrypoint-initdb.d"),
    ],
    ids=["policies", "connectors", "litellm-config", "writable"],
)
def test_a_repository_bind_mount_is_reported(service: str, volume: str) -> None:
    compose = copy.deepcopy(load_prod())
    compose["services"][service].setdefault("volumes", []).append(volume)

    assert repository_mount_problems(compose) != []


def test_litellm_runs_the_baked_in_prod_config_and_publishes_no_port() -> None:
    litellm = load_prod()["services"]["litellm"]

    assert litellm["image"].startswith("ais0c-litellm:" + OWN_TAG)
    assert litellm["command"][:2] == ["--config", "/etc/litellm/litellm.prod.yaml"]
    assert "volumes" not in litellm
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
