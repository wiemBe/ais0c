"""Platform images (T-076): deploy/images/platform.Dockerfile and ui.Dockerfile.

Static checks of the Dockerfiles, the .dockerignore files and the nginx configuration, and a real
build of both images (skipped without docker; it needs the network). The FROM-line helpers are
the ones test_compose_file.py uses for the dev stack's built images.
"""

import importlib.util
import re
import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_compose_checks() -> ModuleType:
    """test_compose_file.py as a module: the tests run without a package, so no plain import."""
    path = Path(__file__).with_name("test_compose_file.py")
    spec = importlib.util.spec_from_file_location("ais0c_test_compose_file", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_compose_checks = load_compose_checks()
EXAMPLE_DIGEST: str = _compose_checks.EXAMPLE_DIGEST
dockerfile_images: Callable[[str], list[str]] = _compose_checks.dockerfile_images
image_problems: Callable[[str], list[str]] = _compose_checks.image_problems

IMAGES_DIR = REPO_ROOT / "deploy/images"
PLATFORM = IMAGES_DIR / "platform.Dockerfile"
UI = IMAGES_DIR / "ui.Dockerfile"
NGINX_CONF = IMAGES_DIR / "nginx/ui.conf"
DOCKERFILES = [PLATFORM, UI]
DOCKERIGNORES = [
    IMAGES_DIR / "platform.Dockerfile.dockerignore",
    IMAGES_DIR / "ui.Dockerfile.dockerignore",
]


def instructions(dockerfile: Path) -> list[list[str]]:
    """The Dockerfile's instructions as word lists, continuation lines joined, comments out."""
    text = dockerfile.read_text(encoding="utf-8").replace("\\\n", " ")
    rows = [line.split() for line in text.splitlines()]
    return [row for row in rows if row and not row[0].startswith("#")]


def final_stage(dockerfile: Path) -> list[list[str]]:
    rows = instructions(dockerfile)
    last = max(i for i, row in enumerate(rows) if row[0].upper() == "FROM")
    return rows[last:]


def nginx_directives(config: Path) -> list[str]:
    lines = (line.split("#")[0].strip() for line in config.read_text("utf-8").splitlines())
    return [line for line in lines if line]


# --- 1. pinned base images -------------------------------------------------------------------


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda path: path.name)
def test_every_from_line_is_pinned_by_tag_and_digest(dockerfile: Path) -> None:
    images = dockerfile_images(dockerfile.read_text(encoding="utf-8"))

    assert images
    assert {image: image_problems(image) for image in images if image_problems(image)} == {}


def test_unpinned_from_line_is_reported() -> None:
    dockerfile = (
        f"FROM python:3.12-slim@{EXAMPLE_DIGEST} AS good\n"
        "FROM docker.io/library/node:22 AS no_digest\n"
        f"FROM nginx@{EXAMPLE_DIGEST}\n"
        "COPY --from=good /a /b\n"
    )

    images = dockerfile_images(dockerfile)

    assert [image for image in images if image_problems(image)] == [
        "docker.io/library/node:22",
        f"nginx@{EXAMPLE_DIGEST}",
    ]


def test_platform_image_reuses_the_gateway_base_images() -> None:
    gateway = REPO_ROOT / "services/mcp-gateway/Dockerfile"

    assert dockerfile_images(PLATFORM.read_text("utf-8")) == dockerfile_images(
        gateway.read_text("utf-8")
    )


# --- 2. no root ------------------------------------------------------------------------------


def test_platform_image_runs_as_10001() -> None:
    rows = final_stage(PLATFORM)

    users = [row[1] for row in rows if row[0].upper() == "USER"]
    assert users == ["10001:10001"]
    assert any(row[0].upper() == "RUN" and "--uid 10001" in " ".join(row) for row in rows)


def test_ui_image_runs_unprivileged() -> None:
    rows = final_stage(UI)

    base = next(row[1] for row in rows if row[0].upper() == "FROM")
    assert base.startswith("docker.io/nginxinc/nginx-unprivileged:")
    users = [row[1] for row in rows if row[0].upper() == "USER"]
    assert users == ["101"]


# --- 3. no secret, no environment --------------------------------------------------------------


FORBIDDEN_IN_CONTEXT = ("deploy/compose/.env", "deploy/compose/secrets", ".git")


def reopened(lines: list[str]) -> list[str]:
    """The `!` lines that bring a forbidden path back into the context."""
    found: list[str] = []
    for line in lines:
        if not line.startswith("!"):
            continue
        allowed = line[1:].rstrip("/")
        for forbidden in FORBIDDEN_IN_CONTEXT:
            if (
                allowed == forbidden
                or forbidden.startswith(allowed + "/")
                or allowed.startswith(forbidden + "/")
                or allowed in ("*", "**", "deploy", "deploy/compose")
            ):
                found.append(line)
                break
    return found


@pytest.mark.parametrize("dockerignore", DOCKERIGNORES, ids=lambda path: path.name)
def test_dockerignore_keeps_secrets_out(dockerignore: Path) -> None:
    lines = [
        line.strip()
        for line in dockerignore.read_text("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert lines[0] == "*"
    assert reopened(lines) == []


def test_dockerignore_check_catches_a_reopened_secret() -> None:
    lines = ["*", "!prompts", "!deploy/compose/secrets", "!deploy/compose", "!.git"]

    assert reopened(lines) == lines[2:]


def test_platform_image_has_no_litellm_config() -> None:
    copied = [
        word
        for row in instructions(PLATFORM)
        if row[0].upper() == "COPY"
        for word in row[1:]
        if not word.startswith("--")
    ]

    assert not [word for word in copied if "litellm" in word]
    ignore = (IMAGES_DIR / "platform.Dockerfile.dockerignore").read_text("utf-8")
    patterns = [line for line in ignore.splitlines() if line and not line.startswith("#")]
    assert not [line for line in patterns if "litellm" in line]


# --- 4. content --------------------------------------------------------------------------------


def test_platform_image_carries_the_approved_content() -> None:
    rows = final_stage(PLATFORM)

    copies = {tuple(row[1:]) for row in rows if row[0].upper() == "COPY"}
    for source in ("config/agents", "config/models", "prompts", "skills"):
        assert (source, f"/app/{source}") in copies
        assert (REPO_ROOT / source).is_dir()
    env = " ".join(" ".join(row) for row in rows if row[0].upper() == "ENV")
    assert re.search(r"\bAIS0C_WORKER_ROOT=/app\b", env)
    assert [row[1] for row in rows if row[0].upper() == "WORKDIR"] == ["/app"]
    assert [row[1:] for row in rows if row[0].upper() == "ENTRYPOINT"] == [['["python",', '"-m"]']]
    assert not [row for row in rows if row[0].upper() == "HEALTHCHECK"]


# --- 5. nginx ----------------------------------------------------------------------------------


def test_nginx_serves_only_tls() -> None:
    directives = nginx_directives(NGINX_CONF)

    assert [d for d in directives if d.startswith("listen")] == ["listen 8443 ssl;"]
    assert "ssl_certificate     /run/secrets/ui-tls.crt;" in directives
    assert "ssl_certificate_key /run/secrets/ui-tls.key;" in directives
    assert "ssl_protocols       TLSv1.2 TLSv1.3;" in directives
    assert "server_tokens off;" in directives


def test_nginx_proxies_api_to_the_api_service() -> None:
    directives = nginx_directives(NGINX_CONF)

    assert "location /api/ {" in directives
    assert "proxy_pass http://api:8000;" in directives
    for header in ("Host $host", "X-Forwarded-For $proxy_add_x_forwarded_for"):
        assert f"proxy_set_header {header};" in directives
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in directives
    assert "try_files $uri /index.html;" in directives


def test_nginx_sends_the_security_headers() -> None:
    directives = nginx_directives(NGINX_CONF)

    for header in (
        'X-Content-Type-Options "nosniff"',
        'X-Frame-Options "DENY"',
        'Referrer-Policy "no-referrer"',
        "Content-Security-Policy \"default-src 'self'\"",
    ):
        assert f"add_header {header} always;" in directives


# --- 6. real build -----------------------------------------------------------------------------


def docker(*arguments: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    path = shutil.which("docker")
    assert path is not None
    return subprocess.run(  # noqa: S603
        [path, *arguments], capture_output=True, text=True, timeout=timeout, check=False
    )


@pytest.fixture(scope="module")
def built_images() -> Iterator[dict[str, str]]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    tags = {"platform": "ais0c-platform:test-t076", "ui": "ais0c-ui:test-t076"}
    try:
        for dockerfile, tag in ((PLATFORM, tags["platform"]), (UI, tags["ui"])):
            built = docker("build", "-f", str(dockerfile), "-t", tag, str(REPO_ROOT), timeout=1500)
            assert built.returncode == 0, built.stderr[-2000:]
        yield tags
    finally:
        docker("rmi", *tags.values())


def test_platform_image_builds_and_imports(built_images: dict[str, str]) -> None:
    result = docker(
        "run", "--rm", "--entrypoint", "python", built_images["platform"],
        "-c", "import ais0c_worker, ais0c_api",
    )  # fmt: skip

    assert result.returncode == 0, result.stderr


def test_ui_image_builds(built_images: dict[str, str]) -> None:
    result = docker(
        "run", "--rm", "--entrypoint", "ls", built_images["ui"], "/usr/share/nginx/html/index.html"
    )

    assert result.returncode == 0, result.stderr
