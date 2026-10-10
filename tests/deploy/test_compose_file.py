"""Static checks of the dev stack in deploy/compose/ (T-003 acceptance criteria 1, 8 and 9).

They keep every image pinned, every secret in the environment and .env out of git, ports on
localhost and every long-running service under a healthcheck. Each rule also runs against a
broken input to show that it catches the problem. The running stack is checked by
test_dev_stack.py.

T-018 added images built on this machine (pinned by tag, with pinned base images) and secret
files (a <NAME>_FILE variable may name one); its own checks of the "qradar" profile are in
services/mcp-gateway/tests/test_deploy.py.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_DIR = REPO_ROOT / "deploy/compose"
COMPOSE_FILE = COMPOSE_DIR / "docker-compose.dev.yaml"
ENV_EXAMPLE = COMPOSE_DIR / ".env.example"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
SECRET_NAME = re.compile(r"PASSWORD|PWD|SECRET|TOKEN|(^|_)KEY$", re.IGNORECASE)
# ${NAME}, ${NAME:-default}, ${NAME:?message} and the forms without the colon.
INTERPOLATION = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:?[-?+])(?P<arg>[^}]*))?\}"
)
EXAMPLE_DIGEST = "sha256:" + "0" * 64


def load_compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


def executable(name: str) -> str:
    path = shutil.which(name)
    assert path is not None, f"{name} is not installed"
    return path


# --- images (criterion 8) -----------------------------------------------------------------


def split_image(reference: str) -> tuple[str, str | None, str | None]:
    """Split name[:tag][@digest]. A colon before the last slash belongs to a registry port."""
    name, _, digest = reference.partition("@")
    colon = name.rfind(":")
    if colon > name.rfind("/"):
        return name[:colon], name[colon + 1 :], digest or None
    return name, None, digest or None


def image_problems(reference: str, *, local: bool = False) -> list[str]:
    """What keeps `reference` from being pinned. A `local` image, built on this machine and
    never pulled, has no registry digest; it needs only a fixed tag."""
    if "$" in reference:
        return ["is set through a variable"]
    _, tag, digest = split_image(reference)
    problems: list[str] = []
    if tag is None:
        problems.append("has no tag")
    elif "latest" in tag:
        problems.append(f"uses the floating tag {tag!r}")
    if not local and (digest is None or not DIGEST.fullmatch(digest)):
        problems.append("is not pinned by a sha256 digest")
    return problems


def image_references(compose: dict[str, Any]) -> dict[str, str]:
    """Map each place that names an image ("service" or "service:/target") to the image."""
    references: dict[str, str] = {}
    for name, service in compose["services"].items():
        if "image" in service:
            references[name] = service["image"]
        for volume in service.get("volumes", []):
            if isinstance(volume, dict) and volume.get("type") == "image":
                references[f"{name}:{volume['target']}"] = volume["source"]
    return references


# T-018: images built on this machine. Compose never pulls them: `build` builds the image from
# this checkout, whose Dockerfile pins its base images; `never` uses an image built elsewhere,
# such as the qradar-mcp fork, and fails when it is missing.
LOCAL_PULL_POLICIES = {"build", "never"}


def local_services(compose: dict[str, Any]) -> set[str]:
    return {
        name
        for name, service in compose["services"].items()
        if service.get("pull_policy") in LOCAL_PULL_POLICIES
    }


def test_every_image_is_pinned_by_tag_and_digest() -> None:
    compose = load_compose()
    references = image_references(compose)
    local = local_services(compose)

    assert "otel-collector:/probe" in references  # image volumes are checked too
    problems = {
        where: image_problems(ref, local=where in local) for where, ref in references.items()
    }
    assert {where: found for where, found in problems.items() if found} == {}


def dockerfile_images(dockerfile: str) -> list[str]:
    """The images a Dockerfile pulls: FROM lines and --from= of earlier stages excluded."""
    stages: set[str] = set()
    images: list[str] = []
    for line in dockerfile.replace("\\\n", " ").splitlines():
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        instruction = words[0].upper()
        if instruction == "FROM":
            arguments = [word for word in words[1:] if not word.startswith("--")]
            if arguments[0].lower() not in stages:
                images.append(arguments[0])
            if len(arguments) == 3 and arguments[1].upper() == "AS":
                stages.add(arguments[2].lower())
        elif instruction in ("COPY", "RUN"):
            for word in words[1:]:
                for option in word.split(","):
                    _, found, source = option.rpartition("from=")
                    if found and not source.isdigit() and source.lower() not in stages:
                        images.append(source)
    return images


def built_services(compose: dict[str, Any]) -> dict[str, Path]:
    """Map each service that builds its image to its Dockerfile."""
    return {
        name: COMPOSE_DIR
        / service["build"]["context"]
        / service["build"].get("dockerfile", "Dockerfile")
        for name, service in compose["services"].items()
        if "build" in service
    }


def test_built_images_pin_their_base_images() -> None:
    # A build brings in base images this file does not show; their FROM lines are pinned like
    # the images above, and the built image is never pulled under its own name.
    compose = load_compose()

    for name, dockerfile in built_services(compose).items():
        assert compose["services"][name].get("pull_policy") == "build", name
        images = dockerfile_images(dockerfile.read_text(encoding="utf-8"))
        assert images, name
        problems = {image: image_problems(image) for image in images}
        assert {image: found for image, found in problems.items() if found} == {}, name


def test_dockerfile_images_are_found_and_stages_skipped() -> None:
    dockerfile = (
        f"FROM ghcr.io/astral-sh/uv:0.12.22@{EXAMPLE_DIGEST} AS uv\n"
        "FROM --platform=linux/amd64 python:3.12-slim AS build\n"
        "COPY --from=uv /uv /usr/local/bin/uv\n"
        "COPY --from=docker.io/library/busybox:latest /bin/busybox /bin/\n"
        "RUN --mount=type=bind,from=build,source=/x,target=/y \\\n    true\n"
        "FROM build\n"
    )

    images = dockerfile_images(dockerfile)

    assert images == [
        f"ghcr.io/astral-sh/uv:0.12.22@{EXAMPLE_DIGEST}",
        "python:3.12-slim",
        "docker.io/library/busybox:latest",
    ]
    assert [image for image in images if image_problems(image)] == images[1:]


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("docker.io/library/postgres", ["has no tag", "is not pinned by a sha256 digest"]),
        ("postgres:latest", ["uses the floating tag 'latest'", "is not pinned by a sha256 digest"]),
        (f"postgres:latest@{EXAMPLE_DIGEST}", ["uses the floating tag 'latest'"]),
        (f"otel/collector:latest-amd64@{EXAMPLE_DIGEST}", ["uses the floating tag 'latest-amd64'"]),
        ("ghcr.io/berriai/litellm:v1.103.2", ["is not pinned by a sha256 digest"]),
        (f"localhost:5000/ais0c/api@{EXAMPLE_DIGEST}", ["has no tag"]),
        ("ghcr.io/berriai/litellm:v1.103.2@sha256:abc", ["is not pinned by a sha256 digest"]),
        ("${LITELLM_IMAGE}", ["is set through a variable"]),
    ],
)
def test_unpinned_image_is_rejected(reference: str, expected: list[str]) -> None:
    assert image_problems(reference) == expected


@pytest.mark.parametrize(
    "reference",
    [
        f"docker.io/library/busybox:1.37.0-musl@{EXAMPLE_DIGEST}",
        f"localhost:5000/ais0c/api:0.1.0@{EXAMPLE_DIGEST}",
    ],
)
def test_pinned_image_is_accepted(reference: str) -> None:
    assert image_problems(reference) == []


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("ais0c-mcp-gateway:dev", []),
        ("qradar-mcp-fork:238ab6b9d0d49ba1d137fa70d1a86d05bf00e013", []),
        ("qradar-mcp-fork", ["has no tag"]),
        ("qradar-mcp-fork:latest", ["uses the floating tag 'latest'"]),
    ],
)
def test_local_image_needs_a_fixed_tag(reference: str, expected: list[str]) -> None:
    assert image_problems(reference, local=True) == expected


# --- secrets (criterion 9) ----------------------------------------------------------------


def environment(service: dict[str, Any]) -> dict[str, str | None]:
    """Return a service's environment, written either as a mapping or as NAME=value items."""
    value = service.get("environment") or {}
    if isinstance(value, dict):
        return {str(k): None if v is None else str(v) for k, v in value.items()}
    pairs = (str(item).partition("=") for item in value)
    return {name: (val if sep else None) for name, sep, val in pairs}


def secret_files(service: dict[str, Any]) -> set[str]:
    """The paths of the Compose secrets a service mounts (default /run/secrets/<name>)."""
    paths: set[str] = set()
    for item in service.get("secrets") or []:
        target = item if isinstance(item, str) else item.get("target") or item["source"]
        paths.add(target if target.startswith("/") else f"/run/secrets/{target}")
    return paths


def secret_problems(compose: dict[str, Any]) -> list[str]:
    """Describe secret settings that are not taken from the environment without a default.

    A <NAME>_FILE variable (T-018) may instead name one of the service's own secret files: it
    holds a path, and the secret stays in the file."""
    problems: list[str] = []
    for name, service in compose["services"].items():
        for key, value in environment(service).items():
            # A name without a value passes the variable through from compose's environment.
            if not SECRET_NAME.search(key) or value is None:
                continue
            if key.endswith("_FILE") and INTERPOLATION.fullmatch(value) is None:
                if value not in secret_files(service):
                    problems.append(f"{name}: {key} does not name one of its secret files")
                continue
            match = INTERPOLATION.fullmatch(value)
            if match is None:
                problems.append(f"{name}: {key} is not taken from the environment")
            elif match["op"] in ("-", ":-", "+", ":+") and match["arg"]:
                problems.append(f"{name}: {key} has a value in the compose file")
    return problems


def env_example() -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            name, sep, value = line.partition("=")
            assert sep, f"not a NAME=value line: {line!r}"
            entries[name.strip()] = value
    return entries


def test_secrets_come_only_from_the_environment() -> None:
    assert secret_problems(load_compose()) == []


@pytest.mark.parametrize(
    ("environment_value", "expected"),
    [
        (
            {"POSTGRES_PASSWORD": "temporal"},
            "db: POSTGRES_PASSWORD is not taken from the environment",
        ),
        (["POSTGRES_PWD=temporal"], "db: POSTGRES_PWD is not taken from the environment"),
        (
            {"SQL_PASSWORD": "${SQL_PASSWORD:-temporal}"},
            "db: SQL_PASSWORD has a value in the compose file",
        ),
        (
            {"LITELLM_MASTER_KEY": "sk-${SUFFIX}"},
            "db: LITELLM_MASTER_KEY is not taken from the environment",
        ),
    ],
    ids=["literal", "literal-list-form", "default-value", "partly-literal"],
)
def test_literal_secret_is_rejected(environment_value: object, expected: str) -> None:
    compose = {"services": {"db": {"environment": environment_value}}}

    assert secret_problems(compose) == [expected]


@pytest.mark.parametrize(
    "environment_value",
    [
        {"SECRET_TOKEN": "${SECRET_TOKEN:?Set SECRET_TOKEN}"},
        {"SECRET_TOKEN": "${SECRET_TOKEN:-}"},
        {"SECRET_TOKEN": "${SECRET_TOKEN}"},
        {"SECRET_TOKEN": None},
        ["SECRET_TOKEN"],
    ],
    ids=["required", "empty-default", "plain", "pass-through", "pass-through-list-form"],
)
def test_secret_from_the_environment_is_accepted(environment_value: object) -> None:
    compose = {"services": {"db": {"environment": environment_value}}}

    assert secret_problems(compose) == []


@pytest.mark.parametrize(
    ("secrets", "value", "expected"),
    [
        (["db-token"], "/run/secrets/db-token", []),
        ([{"source": "db-token", "target": "/etc/db/token"}], "/etc/db/token", []),
        ([], "/run/secrets/db-token", ["db: DB_TOKEN_FILE does not name one of its secret files"]),
        (
            ["db-token"],
            "/run/secrets/other",
            ["db: DB_TOKEN_FILE does not name one of its secret files"],
        ),
    ],
    ids=["mounted", "mounted-at-a-target", "not-mounted", "another-file"],
)
def test_a_secret_file_variable_names_a_mounted_secret(
    secrets: list[object], value: str, expected: list[str]
) -> None:
    compose = {"services": {"db": {"environment": {"DB_TOKEN_FILE": value}, "secrets": secrets}}}

    assert secret_problems(compose) == expected


def test_env_example_has_only_empty_values() -> None:
    entries = env_example()

    assert entries
    assert {name: value for name, value in entries.items() if value.strip()} == {}


def test_env_example_lists_every_variable_the_compose_file_reads() -> None:
    used = {match["name"] for match in INTERPOLATION.finditer(COMPOSE_FILE.read_text("utf-8"))}

    assert used == set(env_example())


def test_dot_env_files_are_not_committed() -> None:
    tracked = subprocess.run(  # noqa: S603
        [executable("git"), "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    env_files = [path for path in tracked if Path(path).name.startswith(".env")]

    assert [path for path in env_files if not path.endswith(".example")] == []


# --- services (criterion 1) ---------------------------------------------------------------


def one_shot_jobs(compose: dict[str, Any]) -> set[str]:
    """Return the services that others wait for with service_completed_successfully."""
    return {
        dependency
        for service in compose["services"].values()
        for dependency, condition in (service.get("depends_on") or {}).items()
        if isinstance(condition, dict)
        and condition.get("condition") == "service_completed_successfully"
    }


def test_every_long_running_service_has_a_healthcheck() -> None:
    compose = load_compose()
    jobs = one_shot_jobs(compose)

    for name, service in compose["services"].items():
        if name in jobs:
            assert service.get("restart") == "no", name
            continue
        healthcheck = service.get("healthcheck") or {}
        assert healthcheck.get("test"), name
        assert not healthcheck.get("disable"), name


def test_dependencies_wait_for_health_or_completion() -> None:
    for name, service in load_compose()["services"].items():
        for dependency, condition in (service.get("depends_on") or {}).items():
            assert isinstance(condition, dict), f"{name} -> {dependency}: no condition"
            assert condition["condition"] in ("service_healthy", "service_completed_successfully")


def test_ports_are_published_on_localhost_only() -> None:
    # Temporal and its UI have no authentication in the dev stack.
    for name, service in load_compose()["services"].items():
        for port in service.get("ports", []):
            assert isinstance(port, str), f"{name}: use the short syntax with a host IP"
            assert port.startswith("127.0.0.1:"), f"{name}: {port}"


def bind_mounts(compose: dict[str, Any]) -> list[tuple[str, str, str, list[str]]]:
    """Return (service, source, target, options) for every short-syntax bind mount."""
    mounts: list[tuple[str, str, str, list[str]]] = []
    for name, service in compose["services"].items():
        for volume in service.get("volumes", []):
            if isinstance(volume, str) and volume.startswith((".", "/")):
                # A colon inside ${NAME:-default} is not a separator.
                parts = re.split(r":(?![^{]*\})", volume)
                source, target, *options = parts
                mounts.append((name, source, target, ",".join(options).split(",")))
    return mounts


def resolve_default(source: str, env: dict[str, str] | None = None) -> str:
    """Resolve ${NAME:-default} in a bind mount source; an unset or empty value takes the default.

    Any other form (${NAME}, ${NAME-x}, ${NAME:+x}, ${NAME:?x}) raises: Compose can resolve it to
    an empty file name, so a mount source may use only the ":-" operator.
    """
    values = env or {}

    def substitute(match: re.Match[str]) -> str:
        if match["op"] != ":-":
            raise ValueError(f"bind mount source {source!r} uses {match[0]!r}; only ${{NAME:-x}}")
        return values.get(match["name"]) or match["arg"]

    return INTERPOLATION.sub(substitute, source)


@pytest.mark.parametrize("source", ["${X:+a.yaml}", "${X-a.yaml}", "${X}", "${X:?msg}"])
def test_mount_source_with_another_operator_is_reported(source: str) -> None:
    with pytest.raises(ValueError, match="only"):
        resolve_default(f"../../config/{source}")


def test_bind_mounts_are_read_only_and_exist() -> None:
    mounts = bind_mounts(load_compose())

    assert mounts
    for name, source, target, options in mounts:
        assert (COMPOSE_DIR / resolve_default(source)).exists(), f"{name}: {source} does not exist"
        assert "ro" in options, f"{name}: {target} is writable"
        assert "z" in options, f"{name}: {target} needs the SELinux label z"


def litellm_config_source(env: dict[str, str]) -> Path:
    compose = load_compose()
    command = compose["services"]["litellm"]["command"]
    config = command[command.index("--config") + 1]
    mounted = {
        target: source for name, source, target, _ in bind_mounts(compose) if name == "litellm"
    }
    return (COMPOSE_DIR / resolve_default(mounted[config], env)).resolve()


def test_litellm_runs_the_dev_config() -> None:
    assert litellm_config_source({}) == REPO_ROOT / "config/litellm/litellm.dev.yaml"
    assert litellm_config_source({"AIS0C_LITELLM_CONFIG": ""}).name == "litellm.dev.yaml"


def test_litellm_config_variable_selects_the_free_config() -> None:
    source = litellm_config_source({"AIS0C_LITELLM_CONFIG": "litellm.dev-free.yaml"})

    assert source == REPO_ROOT / "config/litellm/litellm.dev-free.yaml"
    assert source.exists()


def test_litellm_receives_the_opencode_zen_key_from_the_environment() -> None:
    environment = load_compose()["services"]["litellm"]["environment"]

    assert environment["OPENCODE_ZEN_API_KEY"] == "${OPENCODE_ZEN_API_KEY:-}"
    assert {"AIS0C_LITELLM_CONFIG", "OPENCODE_ZEN_API_KEY"} <= set(env_example())


# --- docker compose -----------------------------------------------------------------------


def compose_config(env: dict[str, str], env_file: Path) -> subprocess.CompletedProcess[str]:
    """Run `docker compose config` with only the given variables; deploy/compose/.env is not read."""
    base = {key: value for key, value in os.environ.items() if key not in env_example()}
    return subprocess.run(  # noqa: S603
        [
            executable("docker"),
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(COMPOSE_FILE),
            "config",
            "--quiet",
        ],
        env=base | env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def has_docker_compose() -> bool:
    docker = shutil.which("docker")
    if docker is None:
        return False
    version = subprocess.run([docker, "compose", "version"], capture_output=True, check=False)  # noqa: S603
    return version.returncode == 0


needs_compose = pytest.mark.skipif(
    not has_docker_compose(), reason="docker compose is not installed"
)


@needs_compose
def test_docker_compose_accepts_the_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.env"
    empty.touch()

    result = compose_config({name: "placeholder" for name in env_example()}, empty)

    assert result.returncode == 0, result.stderr


@needs_compose
def test_docker_compose_stops_without_a_required_secret(tmp_path: Path) -> None:
    empty = tmp_path / "empty.env"
    empty.touch()
    env = {name: "placeholder" for name in env_example() if name != "POSTGRES_PASSWORD"}

    result = compose_config(env, empty)

    assert result.returncode != 0
    assert "Set POSTGRES_PASSWORD in deploy/compose/.env" in result.stderr
