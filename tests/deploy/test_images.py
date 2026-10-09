"""Platform images (T-076): deploy/images/platform.Dockerfile and ui.Dockerfile.

Static checks of the Dockerfiles, the .dockerignore files and the nginx configuration, and a real
build of both images (skipped without docker; it needs the network). The FROM-line helpers are
the ones test_compose_file.py uses for the dev stack's built images.
"""

import importlib.util
import json
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
GATEWAY = REPO_ROOT / "services/mcp-gateway/Dockerfile"
LITELLM = IMAGES_DIR / "litellm.Dockerfile"
DOCKERFILES = [PLATFORM, UI, GATEWAY, LITELLM]
DOCKERIGNORES = [
    IMAGES_DIR / "platform.Dockerfile.dockerignore",
    IMAGES_DIR / "ui.Dockerfile.dockerignore",
    REPO_ROOT / "services/mcp-gateway/Dockerfile.dockerignore",
    IMAGES_DIR / "litellm.Dockerfile.dockerignore",
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


def ignore_patterns(dockerignore: Path) -> list[str]:
    return [
        line.strip()
        for line in dockerignore.read_text("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def glob_regex(pattern: str) -> re.Pattern[str]:
    """A .dockerignore pattern as a regex: `**` crosses slashes, `*` does not; a match on a
    directory also covers everything below it."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + "(?:/.*)?")


def in_context(patterns: list[str], path: str) -> bool:
    """Whether the build context includes `path`: the last matching pattern wins, and `!`
    includes. A file is also kept out when a parent directory is excluded last."""
    included = False
    for pattern in patterns:
        negate = pattern.startswith("!")
        if glob_regex(pattern.removeprefix("!").rstrip("/")).fullmatch(path):
            included = negate
    return included


FORBIDDEN_IN_CONTEXT = (
    "deploy/compose/.env",
    "deploy/compose/secrets/db-password",
    ".git/config",
    "apps/ui/.env",
    "apps/ui/.env.local",
    "apps/ui/.env.production",
    "config/agents/.env",
    "config/models/.env.local",
    "prompts/.env",
    "skills/.env",
    "packages/agents/src/.env",
)


@pytest.mark.parametrize("dockerignore", DOCKERIGNORES, ids=lambda path: path.name)
def test_dockerignore_keeps_secrets_out(dockerignore: Path) -> None:
    patterns = ignore_patterns(dockerignore)

    assert patterns[0] == "*"
    assert [path for path in FORBIDDEN_IN_CONTEXT if in_context(patterns, path)] == []


def test_dockerignore_rules_include_what_the_images_need() -> None:
    platform = ignore_patterns(DOCKERIGNORES[0])
    ui = ignore_patterns(DOCKERIGNORES[1])

    for path in ("uv.lock", "config/agents/triage.yaml", "prompts/x.md", "skills/a/SKILL.md"):
        assert in_context(platform, path), path
    for path in ("apps/ui/package.json", "apps/ui/src/main.tsx", "deploy/images/nginx/ui.conf"):
        assert in_context(ui, path), path


def test_platform_dockerignore_drops_litellm_even_after_a_broad_reopen() -> None:
    patterns = ignore_patterns(DOCKERIGNORES[0])
    broad = ["*", "!config", *patterns[1:]]

    assert not in_context(broad, "config/litellm/config.yaml")
    assert in_context(broad, "config/agents/triage.yaml")
    # Without the closing exclusions, the same re-open would let it in.
    assert in_context(["*", "!config"], "config/litellm/config.yaml")


def test_dockerignore_check_catches_a_reopened_secret() -> None:
    for broad in ("!config", "!config/**", "!deploy/**", "!**", "!deploy/compose", "!.git"):
        patterns = ["*", broad]
        leaked = [path for path in FORBIDDEN_IN_CONTEXT if in_context(patterns, path)]
        assert leaked, broad


def test_platform_image_has_no_litellm_config() -> None:
    copied = [
        word
        for row in instructions(PLATFORM)
        if row[0].upper() == "COPY"
        for word in row[1:]
        if not word.startswith("--")
    ]

    assert not [word for word in copied if "litellm" in word]
    assert not in_context(ignore_patterns(DOCKERIGNORES[0]), "config/litellm/config.yaml")


APPROVED = (
    "config/agents",
    "config/models",
    "config/policies",
    "config/sigma",
    "config/telemetry",
    "prompts",
    "skills",
)


# --- 4. content --------------------------------------------------------------------------------


def test_platform_image_carries_the_approved_content() -> None:
    rows = final_stage(PLATFORM)

    copies = {tuple(row[1:]) for row in rows if row[0].upper() == "COPY"}
    # Exactly the venv and the approved trees; nothing else enters the final stage.
    assert copies == {
        ("--from=build", "/opt/venv", "/opt/venv"),
        *{(source, f"/app/{source}") for source in APPROVED},
    }
    for source in APPROVED:
        assert (REPO_ROOT / source).is_dir()
    env = " ".join(" ".join(row) for row in rows if row[0].upper() == "ENV")
    assert re.search(r"\bAIS0C_WORKER_ROOT=/app\b", env)
    assert [row[1] for row in rows if row[0].upper() == "WORKDIR"] == ["/app"]
    assert [row[1:] for row in rows if row[0].upper() == "ENTRYPOINT"] == [['["python",', '"-m"]']]
    assert not [row for row in rows if row[0].upper() == "HEALTHCHECK"]


def copies_of(dockerfile: Path) -> set[tuple[str, ...]]:
    return {tuple(row[1:]) for row in final_stage(dockerfile) if row[0].upper() == "COPY"}


def test_gateway_image_carries_its_connectors_and_policies() -> None:
    patterns = ignore_patterns(DOCKERIGNORES[2])

    assert copies_of(GATEWAY) == {
        ("--from=build", "/opt/venv", "/opt/venv"),
        ("config/connectors", "/etc/ais0c/connectors"),
        ("config/policies", "/etc/ais0c/policies"),
    }
    for path in ("config/connectors/qradar.yaml", "config/policies/qradar.yaml"):
        assert in_context(patterns, path), path
    assert not in_context(patterns, "config/agents/triage.yaml")
    assert not in_context(patterns, "config/connectors/.env")


def test_litellm_image_adds_only_the_prod_config_to_the_dev_base_image() -> None:
    dev = (REPO_ROOT / "deploy/compose/docker-compose.dev.yaml").read_text("utf-8")
    patterns = ignore_patterns(DOCKERIGNORES[3])
    rows = final_stage(LITELLM)

    assert dockerfile_images(LITELLM.read_text("utf-8")) == [rows[0][1]]
    assert f"image: {rows[0][1]}" in dev
    assert copies_of(LITELLM) == {
        ("config/litellm/litellm.prod.yaml", "/etc/litellm/litellm.prod.yaml")
    }
    assert {row[0].upper() for row in rows} <= {"FROM", "LABEL", "COPY"}
    assert in_context(patterns, "config/litellm/litellm.prod.yaml")
    assert not in_context(patterns, "config/litellm/litellm.dev.yaml")
    assert not in_context(patterns, "config/litellm/.env")


# --- 5. nginx ----------------------------------------------------------------------------------


def test_nginx_serves_only_tls() -> None:
    directives = nginx_directives(NGINX_CONF)

    assert [d for d in directives if d.startswith("listen")] == ["listen 8443 ssl;"]
    assert "ssl_certificate     /run/secrets/ui-tls.crt;" in directives
    assert "ssl_certificate_key /run/secrets/ui-tls.key;" in directives
    assert "ssl_protocols       TLSv1.2 TLSv1.3;" in directives
    assert "server_tokens off;" in directives
    assert "root /usr/share/nginx/html;" in directives


def test_nginx_proxies_api_to_the_api_service() -> None:
    directives = nginx_directives(NGINX_CONF)

    assert "location /api/ {" in directives
    assert [d for d in directives if d.startswith("proxy_pass")] == ["proxy_pass http://api:8000;"]
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


def test_ui_package_manager_is_pinned_by_hash() -> None:
    # Corepack checks the downloaded pnpm against this hash.
    package = json.loads((REPO_ROOT / "apps/ui/package.json").read_text("utf-8"))

    assert re.fullmatch(r"pnpm@\d+\.\d+\.\d+\+sha512\.[0-9a-f]{128}", package["packageManager"])


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
    tags = {
        "platform": "ais0c-platform:test-t076",
        "ui": "ais0c-ui:test-t076",
        "gateway": "ais0c-mcp-gateway:test-t076",
        "litellm": "ais0c-litellm:test-t076",
    }
    try:
        for dockerfile, tag in (
            (PLATFORM, tags["platform"]),
            (UI, tags["ui"]),
            (GATEWAY, tags["gateway"]),
            (LITELLM, tags["litellm"]),
        ):
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


def test_release_content_is_inside_the_built_images(built_images: dict[str, str]) -> None:
    for image, path in (
        ("platform", "/app/config/policies/qradar.yaml"),
        ("platform", "/app/config/sigma/qradar-pipeline.yaml"),
        ("platform", "/app/config/telemetry/log-source-classes.yaml"),
        ("gateway", "/etc/ais0c/connectors/qradar.yaml"),
        ("gateway", "/etc/ais0c/policies/qradar.yaml"),
        ("litellm", "/etc/litellm/litellm.prod.yaml"),
    ):
        result = docker("run", "--rm", "--entrypoint", "ls", built_images[image], path)
        assert result.returncode == 0, f"{image}: {path}: {result.stderr}"
