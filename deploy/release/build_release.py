"""Build the self-contained production shadow release package (T-081).

The package contains every image named by the production Compose file, the small set of
installation files needed on the server, checksums and a Turkish release manifest. Docker
output is streamed through gzip; image archives are never accumulated in memory.
"""

import argparse
import gzip
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml

HERE: Final = Path(__file__).resolve().parent
ROOT: Final = HERE.parents[1]
COMPOSE_FILE: Final = ROOT / "deploy/compose/docker-compose.prod.yaml"
CONNECTOR_FILE: Final = ROOT / "config/connectors/qradar.yaml"
VERSION_PATTERN: Final = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$")
_VERSION_REFERENCE: Final = re.compile(r"\$\{AIS0C_VERSION(?::\?[^}]*)?\}")
OWN_IMAGES: Final = (
    "ais0c-platform",
    "ais0c-ui",
    "ais0c-mcp-gateway",
    "ais0c-litellm",
    "qradar-mcp-fork",
)
# Image name -> (Dockerfile, build context), both relative to the repository root. The fork
# (D-46) is a subtree with its own Dockerfile and context; the others build from the root.
BUILDS: Final = {
    "ais0c-platform": ("deploy/images/platform.Dockerfile", "."),
    "ais0c-ui": ("deploy/images/ui.Dockerfile", "."),
    "ais0c-mcp-gateway": ("services/mcp-gateway/Dockerfile", "."),
    "ais0c-litellm": ("deploy/images/litellm.Dockerfile", "."),
    "qradar-mcp-fork": ("services/qradar-mcp/Dockerfile", "services/qradar-mcp"),
}
ARCHIVED: Final = (
    "deploy/compose/docker-compose.prod.yaml",
    "deploy/compose/.env.prod.example",
    "deploy/compose/make_secrets.py",
    "deploy/compose/README.md",
    "deploy/compose/postgres",
    "deploy/compose/temporal",
    "deploy/compose/otel-collector",
    "config/connectors/qradar.yaml",
    "docs/impl/deploy-prod-shadow.md",
)


@dataclass(frozen=True)
class ReleasePlan:
    """Immutable release inputs resolved from the checked-out commit."""

    version: str
    commit: str
    server_version: str
    images: tuple[str, ...]


class CommandFailed(RuntimeError):
    """An external release command returned a non-zero status."""

    def __init__(self, command: Sequence[str], returncode: int) -> None:
        super().__init__(_command_text(command))
        self.command = tuple(command)
        self.returncode = returncode


class ImagePinMismatch(RuntimeError):
    """A local image tag does not resolve to its Compose-pinned digest."""

    def __init__(self, image: str) -> None:
        super().__init__(f"image tag does not match pinned digest: {image}")
        self.image = image


def check_version(value: str) -> str:
    """Return a safe release version, or reject it."""
    if VERSION_PATTERN.fullmatch(value) is None:
        raise ValueError(f"invalid release version: {value!r}")
    return value


def save_reference(reference: str) -> str:
    """Return the tagged reference Docker must save for a digest-pinned image."""
    name, separator, digest = reference.rpartition("@sha256:")
    if separator and name and re.fullmatch(r"[0-9A-Fa-f]{64}", digest):
        return name
    return reference


def repo_digest_matches(reference: str, repo_digests: Sequence[str]) -> bool:
    """Whether RepoDigests contains the repository and digest pinned by `reference`."""
    expected = _repository_digest(reference)
    if expected is None:
        return False
    return any(_repository_digest(repo_digest) == expected for repo_digest in repo_digests)


def compose_images(compose: Mapping[str, Any], version: str) -> tuple[str, ...]:
    """List every resolved service image and image-volume source, sorted and unique."""
    check_version(version)
    services = compose.get("services")
    if not isinstance(services, Mapping):
        raise ValueError("production Compose has no services mapping")

    references: list[str] = []
    for service_name, raw_service in services.items():
        if not isinstance(raw_service, Mapping):
            raise ValueError(f"service {service_name} is not a mapping")
        image = raw_service.get("image")
        if not isinstance(image, str):
            raise ValueError(f"service {service_name} has no image")
        references.append(_resolve_version(image, version))
        volumes = raw_service.get("volumes", ())
        if not isinstance(volumes, Sequence) or isinstance(volumes, str):
            raise ValueError(f"service {service_name} volumes are not a sequence")
        for volume in volumes:
            if not isinstance(volume, Mapping) or volume.get("type") != "image":
                continue
            source = volume.get("source")
            if not isinstance(source, str):
                raise ValueError(f"service {service_name} has an image volume without a source")
            references.append(_resolve_version(source, version))

    for name in OWN_IMAGES:
        expected = f"{name}:{version}"
        wrong = [
            reference
            for reference in references
            if reference != expected
            and (reference == name or reference.startswith((f"{name}:", f"{name}@")))
        ]
        if wrong:
            raise ValueError(f"{name} must use release tag {version}, found {wrong[0]}")
        if expected not in references:
            raise ValueError(f"production Compose does not name {expected}")

    return tuple(sorted(set(references)))


def working_tree_clean(root: Path) -> bool:
    """Whether tracked and untracked files are absent from `git status --porcelain`."""
    done = subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "status", "--porcelain"],  # noqa: S607
        capture_output=True,
        check=False,
        text=True,
    )
    return done.returncode == 0 and not done.stdout.strip()


def plan_release(root: Path, version: str) -> ReleasePlan:
    """Read the checked-out commit, connector pin and production image set."""
    checked_version = check_version(version)
    commit = _git_stdout(root, "rev-parse", "HEAD")
    compose = _yaml_mapping(root / COMPOSE_FILE.relative_to(ROOT))
    connector = _yaml_mapping(root / CONNECTOR_FILE.relative_to(ROOT))
    server_version = connector.get("server_version")
    if not isinstance(server_version, str) or not server_version:
        raise ValueError("connector server_version is missing")
    images = compose_images(compose, checked_version)
    return ReleasePlan(
        version=checked_version,
        commit=commit,
        server_version=server_version,
        images=images,
    )


def build_command(name: str, version: str) -> list[str]:
    """The `docker build` command for one of our own images."""
    dockerfile, context = BUILDS[name]
    return ["docker", "build", "-f", dockerfile, "-t", f"{name}:{version}", context]


def commands(plan: ReleasePlan, root: Path, out: Path, *, build: bool) -> list[list[str]]:
    """Return the ordered, shell-display form of the release commands."""
    result: list[list[str]] = []
    if build:
        result.extend(build_command(name, plan.version) for name in BUILDS)
    result.append(
        [
            "docker",
            "save",
            *(save_reference(image) for image in plan.images),
            "|",
            "gzip",
            ">",
            str(out / f"ais0c-images-{plan.version}.tar.gz"),
        ]
    )
    result.append(
        [
            "git",
            "-C",
            str(root),
            "archive",
            "--format=tar.gz",
            f"--prefix=ais0c-{plan.version}/",
            "-o",
            str(out / f"ais0c-files-{plan.version}.tar.gz"),
            "HEAD",
            "--",
            *ARCHIVED,
        ]
    )
    return result


def write_sha256sums(out: Path, names: Sequence[str]) -> Path:
    """Write GNU sha256sum-compatible entries for files in `out`."""
    destination = out / "SHA256SUMS"
    lines: list[str] = []
    for name in names:
        digest = hashlib.sha256()
        with (out / name).open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        lines.append(f"{digest.hexdigest()}  {name}\n")
    destination.write_text("".join(lines), encoding="utf-8")
    return destination


def render_release_md(
    plan: ReleasePlan, image_ids: Mapping[str, str], sums: Mapping[str, str]
) -> str:
    """Render the Turkish release manifest."""
    created = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    lines = [
        f"# ais0c {plan.version}",
        "",
        f"- Sürüm: `{plan.version}`",
        f"- Commit: `{plan.commit}`",
        f"- Oluşturma zamanı: `{created}`",
        f"- `server_version` (fork commit, araç şemaları): `{plan.server_version}`",
        "",
        "## İmajlar",
        "",
        "| İmaj | Kimlik |",
        "|---|---|",
    ]
    lines.extend(f"| `{image}` | `{image_ids[image]}` |" for image in plan.images)
    lines.extend(["", "## Dosyalar", "", "| Dosya | sha256 |", "|---|---|"])
    lines.extend(f"| `{name}` | `{digest}` |" for name, digest in sums.items())
    lines.extend(
        [
            "",
            (
                f"Kurulum adımları `ais0c-{plan.version}/docs/impl/deploy-prod-shadow.md` "
                "belgesinin §4 bölümündedir."
            ),
            "",
            "## Gate raporu",
            "",
            "Planner doldurur.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a production shadow release package.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = ROOT
    out = args.out.resolve()
    try:
        plan = plan_release(root, args.version)
        if not working_tree_clean(root):
            raise ValueError("working tree is not clean")
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            raise ValueError(f"output directory is not empty: {out}")
    except (
        OSError,
        subprocess.SubprocessError,
        yaml.YAMLError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        print(f"build_release: {_one_line(error)}", file=sys.stderr)
        return 2

    display_commands = commands(plan, root, out, build=not args.skip_build)
    if args.dry_run:
        for command in display_commands:
            print(_command_text(command))
        return 0

    try:
        out.mkdir(parents=True, exist_ok=True)
        if not args.skip_build:
            for name in BUILDS:
                _run(build_command(name, plan.version), cwd=root)

        image_ids = _prepare_images(plan, root)
        images_name = f"ais0c-images-{plan.version}.tar.gz"
        files_name = f"ais0c-files-{plan.version}.tar.gz"
        _save_images(plan, root, out / images_name)
        _run(
            [
                "git",
                "-C",
                str(root),
                "archive",
                "--format=tar.gz",
                f"--prefix=ais0c-{plan.version}/",
                "-o",
                str(out / files_name),
                "HEAD",
                "--",
                *ARCHIVED,
            ],
            cwd=root,
        )
        write_sha256sums(out, (images_name, files_name))
        sums = {name: _sha256(out / name) for name in (images_name, files_name)}
        (out / "RELEASE.md").write_text(render_release_md(plan, image_ids, sums), encoding="utf-8")
    except CommandFailed as error:
        print(
            f"build_release: command failed (exit {error.returncode}): "
            f"{_command_text(error.command)}",
            file=sys.stderr,
        )
        return 1
    except (ImagePinMismatch, OSError, subprocess.SubprocessError) as error:
        print(f"build_release: release failed: {_one_line(error)}", file=sys.stderr)
        return 1

    print(out)
    return 0


def _resolve_version(reference: str, version: str) -> str:
    resolved = _VERSION_REFERENCE.sub(version, reference)
    if "${" in resolved:
        raise ValueError(f"unresolved variable in image reference: {reference}")
    return resolved


def _repository_digest(reference: str) -> tuple[str, str] | None:
    name, separator, digest = reference.rpartition("@sha256:")
    if not separator or not name or re.fullmatch(r"[0-9A-Fa-f]{64}", digest) is None:
        return None
    last_slash = name.rfind("/")
    last_colon = name.rfind(":")
    repository = name[:last_colon] if last_colon > last_slash else name
    return _normalized_repository(repository), digest.lower()


def _normalized_repository(repository: str) -> str:
    if repository.startswith("docker.io/"):
        docker_path = repository.removeprefix("docker.io/")
    else:
        first_component = repository.partition("/")[0]
        if "/" in repository and (
            "." in first_component or ":" in first_component or first_component == "localhost"
        ):
            return repository
        docker_path = repository
    if "/" not in docker_path:
        docker_path = f"library/{docker_path}"
    return f"docker.io/{docker_path}"


def _yaml_mapping(path: Path) -> Mapping[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise ValueError(f"{path} does not contain a mapping")
    return loaded


def _git_stdout(root: Path, *arguments: str) -> str:
    command = ["git", "-C", str(root), *arguments]
    done = subprocess.run(  # noqa: S603
        command, capture_output=True, check=False, text=True
    )
    if done.returncode != 0:
        raise ValueError(f"git command failed (exit {done.returncode}): {_command_text(command)}")
    return done.stdout.strip()


def _run(command: Sequence[str], *, cwd: Path) -> None:
    done = subprocess.run(command, cwd=cwd, check=False)  # noqa: S603
    if done.returncode != 0:
        raise CommandFailed(command, done.returncode)


def _prepare_images(plan: ReleasePlan, root: Path) -> dict[str, str]:
    local_images = {f"{name}:{plan.version}" for name in OWN_IMAGES}
    image_ids: dict[str, str] = {}
    for image in plan.images:
        saved_image = save_reference(image)
        if saved_image != image:
            digest_returncode, repo_digests = _inspect_repo_digests(saved_image, root)
            if digest_returncode != 0:
                _run(["docker", "pull", image], cwd=root)
                digest_returncode, repo_digests = _inspect_repo_digests(saved_image, root)
            if digest_returncode != 0:
                raise CommandFailed(
                    [
                        "docker",
                        "image",
                        "inspect",
                        "--format",
                        "{{json .RepoDigests}}",
                        saved_image,
                    ],
                    digest_returncode,
                )
            if not repo_digest_matches(image, repo_digests):
                raise ImagePinMismatch(image)

        inspect_returncode, image_id = _inspect_image(saved_image, root)
        if inspect_returncode != 0 and image not in local_images and saved_image == image:
            _run(["docker", "pull", image], cwd=root)
            inspect_returncode, image_id = _inspect_image(saved_image, root)
        if inspect_returncode != 0:
            raise CommandFailed(
                ["docker", "image", "inspect", "--format", "{{.Id}}", saved_image],
                inspect_returncode,
            )
        image_ids[image] = image_id
    return image_ids


def _inspect_image(image: str, root: Path) -> tuple[int, str]:
    done = subprocess.run(  # noqa: S603
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=False,
        text=True,
    )
    return done.returncode, done.stdout.strip()


def _inspect_repo_digests(image: str, root: Path) -> tuple[int, tuple[str, ...]]:
    done = subprocess.run(  # noqa: S603
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=False,
        text=True,
    )
    if done.returncode != 0:
        return done.returncode, ()
    try:
        loaded = json.loads(done.stdout)
    except json.JSONDecodeError:
        return 1, ()
    if not isinstance(loaded, list):
        return 1, ()
    repo_digests: list[str] = []
    for value in loaded:
        if not isinstance(value, str):
            return 1, ()
        repo_digests.append(value)
    return 0, tuple(repo_digests)


def _save_images(plan: ReleasePlan, root: Path, destination: Path) -> None:
    command = ["docker", "save", *(save_reference(image) for image in plan.images)]
    process = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE)  # noqa: S603
    if process.stdout is None:
        raise RuntimeError("docker save stdout pipe was not created")
    try:
        with gzip.open(destination, "wb") as compressed:
            shutil.copyfileobj(process.stdout, compressed, length=1024 * 1024)
    finally:
        process.stdout.close()
    returncode = process.wait()
    if returncode != 0:
        raise CommandFailed(command, returncode)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _command_text(command: Sequence[str]) -> str:
    return " ".join(token if token in {"|", ">"} else shlex.quote(token) for token in command)


def _one_line(error: BaseException) -> str:
    return " ".join(str(error).splitlines())


if __name__ == "__main__":
    sys.exit(main())
