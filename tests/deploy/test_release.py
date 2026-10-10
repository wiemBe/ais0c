"""Release package builder checks (T-081 acceptance criteria 1 to 8)."""

# All subprocesses in this file invoke fixed test tools with argument lists.
# ruff: noqa: S603, S607

import copy
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy/release"))

import build_release  # noqa: E402  # pyright: ignore[reportMissingImports]
from build_release import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    ARCHIVED,
    OWN_IMAGES,
    ReleasePlan,
    check_version,
    compose_images,
    main,
    render_release_md,
    repo_digest_matches,
    save_reference,
    working_tree_clean,
    write_sha256sums,
)

PROD_COMPOSE = ROOT / "deploy/compose/docker-compose.prod.yaml"
CONNECTOR = ROOT / "config/connectors/qradar.yaml"


@pytest.mark.parametrize("version", ["0.1.0", "0.1.0-shadow1", "1.2.3-rc.1"])
def test_version_pattern(version: str) -> None:
    assert check_version(version) == version


@pytest.mark.parametrize("version", ["latest", "0.1", "v0.1.0", "0.1.0-", "0.1.0;rm -rf /", ""])
def test_version_pattern_refuses_invalid_values(version: str) -> None:
    with pytest.raises(ValueError, match="invalid release version"):
        check_version(version)


def test_compose_images_lists_every_prod_image() -> None:
    compose = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))
    server_version = yaml.safe_load(CONNECTOR.read_text(encoding="utf-8"))["server_version"]

    images = compose_images(compose, "9.9.9-test")

    assert images == tuple(sorted(set(images)))
    for name in OWN_IMAGES:
        assert f"{name}:9.9.9-test" in images
    assert f"qradar-mcp-fork:{server_version}" in images
    assert any(image.startswith("docker.io/pgvector/pgvector:") for image in images)
    assert any(image.startswith("docker.io/library/busybox:") for image in images)
    assert images.count("ais0c-litellm:9.9.9-test") == 1


def test_save_reference_drops_the_digest() -> None:
    compose = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))
    images = compose_images(compose, "9.9.9-test")
    pinned = [image for image in images if "@sha256:" in image]

    assert len(pinned) == 6
    for image in pinned:
        assert save_reference(image) == image.split("@", 1)[0]
    for image in images:
        if image not in pinned:
            assert save_reference(image) == image


def test_repo_digest_matches_with_and_without_the_docker_io_prefix() -> None:
    digest = "a" * 64

    assert repo_digest_matches(
        f"pgvector/pgvector@sha256:{digest}",
        [f"docker.io/pgvector/pgvector:0.8.7-pg18-trixie@sha256:{digest}"],
    )
    assert repo_digest_matches(
        f"busybox@sha256:{digest}",
        [f"docker.io/library/busybox:1.37.0-musl@sha256:{digest}"],
    )
    assert not repo_digest_matches(
        f"busybox@sha256:{digest}",
        [f"docker.io/library/busybox:1.37.0-musl@sha256:{'b' * 64}"],
    )


def test_an_own_image_with_another_tag_is_refused() -> None:
    compose = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))
    broken = copy.deepcopy(compose)
    broken["services"]["ui"]["image"] = "ais0c-ui:latest"

    with pytest.raises(ValueError, match="ais0c-ui"):
        compose_images(broken, "9.9.9-test")


def test_an_unresolved_variable_is_refused() -> None:
    compose = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))
    broken = copy.deepcopy(compose)
    broken["services"]["postgres"]["image"] = "example.com/image:${OTHER}"

    with pytest.raises(ValueError, match="unresolved variable"):
        compose_images(broken, "9.9.9-test")


def test_working_tree_clean(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Release Test"], check=True
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "release@example.com"],
        check=True,
    )
    tracked = repository / "tracked.txt"
    tracked.write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "fixture"], check=True)

    assert working_tree_clean(repository)
    tracked.write_text("changed\n", encoding="utf-8")
    assert not working_tree_clean(repository)
    subprocess.run(["git", "-C", str(repository), "restore", "tracked.txt"], check=True)
    (repository / "untracked.txt").write_text("new\n", encoding="utf-8")
    assert not working_tree_clean(repository)


def test_a_dirty_tree_stops_with_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_release, "working_tree_clean", lambda root: False)

    result = main(["--version", "0.1.0-test", "--out", str(tmp_path / "out"), "--dry-run"])

    assert result == 2
    assert "not clean" in capsys.readouterr().err


def test_a_non_empty_out_dir_stops_with_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "existing").write_text("occupied\n", encoding="utf-8")
    monkeypatch.setattr(build_release, "working_tree_clean", lambda root: True)
    monkeypatch.setattr(build_release, "_fork_has_commit", lambda fork, version: True)

    result = main(["--version", "0.1.0-test", "--out", str(out), "--dry-run"])

    assert result == 2
    assert "not empty" in capsys.readouterr().err


def test_the_default_fork_is_next_to_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed_forks: list[Path] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build_release, "working_tree_clean", lambda root: True)
    monkeypatch.setattr(
        build_release,
        "_fork_has_commit",
        lambda fork, version: observed_forks.append(fork) is None,
    )

    result = main(["--version", "0.1.0-test", "--out", str(tmp_path / "out"), "--dry-run"])

    assert result == 0
    assert observed_forks == [ROOT.parent / "qradar-mcp"]


def test_archived_paths_exist_and_hold_no_secret() -> None:
    archived_files: set[str] = set()
    for path in ARCHIVED:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--", path],
            capture_output=True,
            check=True,
            text=True,
        )
        tracked = {line for line in done.stdout.splitlines() if line}
        assert tracked, f"{path} is absent or untracked"
        archived_files.update(tracked)

    assert not any(path.startswith("deploy/compose/secrets/") for path in archived_files)
    assert not any(
        Path(path).name.startswith(".env") and Path(path).name != ".env.prod.example"
        for path in archived_files
    )
    assert "deploy/compose/docker-compose.dev.yaml" not in archived_files
    assert "deploy/compose/docker-compose.lab.yaml" not in archived_files


def test_dry_run_prints_the_commands_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_release, "working_tree_clean", lambda root: True)
    monkeypatch.setattr(build_release, "_fork_has_commit", lambda fork, version: True)
    out = tmp_path / "release"

    result = main(
        [
            "--version",
            "9.9.9-test",
            "--fork",
            str(tmp_path / "fork"),
            "--out",
            str(out),
            "--dry-run",
        ]
    )

    assert result == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[0:2] for line in lines[:4]] == [["docker", "build"]] * 4
    assert lines[4].startswith("git -C ")
    assert " archive --format=tar " in lines[4]
    assert " | docker build " in lines[4]
    assert lines[5].startswith("docker save ")
    assert " | gzip > " in lines[5]
    assert lines[6].startswith("git -C ")
    assert " archive --format=tar.gz " in lines[6]
    assert not out.exists()

    result = main(
        [
            "--version",
            "9.9.9-test",
            "--fork",
            str(tmp_path / "fork"),
            "--out",
            str(out),
            "--skip-build",
            "--dry-run",
        ]
    )

    assert result == 0
    skip_lines = capsys.readouterr().out.splitlines()
    assert len(skip_lines) == 2
    assert skip_lines[0].startswith("docker save ")
    assert skip_lines[1].startswith("git -C ")
    assert all("docker build" not in line for line in skip_lines)
    assert not out.exists()


def test_dry_run_saves_tag_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_release, "working_tree_clean", lambda root: True)
    monkeypatch.setattr(build_release, "_fork_has_commit", lambda fork, version: True)

    result = main(
        [
            "--version",
            "9.9.9-test",
            "--fork",
            str(tmp_path / "fork"),
            "--out",
            str(tmp_path / "release"),
            "--dry-run",
        ]
    )

    assert result == 0
    save_line = next(
        line for line in capsys.readouterr().out.splitlines() if line.startswith("docker save ")
    )
    assert "@sha256:" not in save_line


def test_a_tag_that_is_not_the_pinned_image_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = build_release.plan_release(ROOT, "0.1.0-test")
    pinned_image = next(image for image in plan.images if "@sha256:" in image)
    monkeypatch.setattr(build_release, "working_tree_clean", lambda root: True)
    monkeypatch.setattr(build_release, "_fork_has_commit", lambda fork, version: True)
    monkeypatch.setattr(build_release, "_inspect_image", lambda image, root: (0, "sha256:id"))
    monkeypatch.setattr(
        build_release,
        "_inspect_repo_digests",
        lambda image, root: (0, (f"{save_reference(image)}@sha256:{'f' * 64}",)),
    )

    result = main(
        [
            "--version",
            plan.version,
            "--fork",
            str(tmp_path / "fork"),
            "--out",
            str(tmp_path / "release"),
            "--skip-build",
        ]
    )

    assert result == 1
    assert pinned_image in capsys.readouterr().err


def test_a_failed_step_exits_1_without_release_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "release"
    monkeypatch.setattr(build_release, "working_tree_clean", lambda root: True)
    monkeypatch.setattr(build_release, "_fork_has_commit", lambda fork, version: True)
    monkeypatch.setattr(
        build_release,
        "_prepare_images",
        lambda plan, root: {
            image: f"sha256:{index:064x}" for index, image in enumerate(plan.images)
        },
    )

    def fail_save(plan: ReleasePlan, root: Path, destination: Path) -> None:
        raise build_release.CommandFailed(["docker", "save", *plan.images], 17)

    monkeypatch.setattr(build_release, "_save_images", fail_save)

    result = main(
        [
            "--version",
            "0.1.0-test",
            "--fork",
            str(tmp_path / "fork"),
            "--out",
            str(out),
            "--skip-build",
        ]
    )

    assert result == 1
    error = capsys.readouterr().err
    assert "docker save" in error
    assert "exit 17" in error
    assert not (out / "RELEASE.md").exists()


def test_a_failing_fork_build_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Process:
        def __init__(self, returncode: int, *, with_stdout: bool = False) -> None:
            self.returncode = returncode
            self.stdout = io.BytesIO() if with_stdout else None

        def wait(self) -> int:
            return self.returncode

    def popen(command: list[str], **kwargs: object) -> Process:
        return Process(23, with_stdout=True) if command[0] == "git" else Process(0)

    plan = build_release.plan_release(ROOT, "0.1.0-test")
    monkeypatch.setattr(build_release.subprocess, "Popen", popen)

    with pytest.raises(build_release.CommandFailed) as raised:
        build_release._build_fork(plan, ROOT, tmp_path / "fork")
    assert raised.value.returncode == 23

    monkeypatch.setattr(build_release, "plan_release", lambda root, version: plan)
    monkeypatch.setattr(build_release, "working_tree_clean", lambda root: True)
    monkeypatch.setattr(build_release, "_fork_has_commit", lambda fork, version: True)
    monkeypatch.setattr(build_release, "_run", lambda command, cwd: None)

    result = main(
        [
            "--version",
            "0.1.0-test",
            "--fork",
            str(tmp_path / "fork"),
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert result == 1


def test_sha256sums_pass_sha256sum_check(tmp_path: Path) -> None:
    names = ("first.tar.gz", "second.tar.gz")
    (tmp_path / names[0]).write_bytes(b"first archive\n")
    (tmp_path / names[1]).write_bytes(b"second archive\n")

    sums = write_sha256sums(tmp_path, names)
    done = subprocess.run(
        ["sha256sum", "-c", sums.name], cwd=tmp_path, capture_output=True, check=False, text=True
    )

    assert done.returncode == 0, done.stderr


def test_release_md_names_version_commit_images_and_sums() -> None:
    plan = ReleasePlan(
        version="1.2.3-test",
        commit="a" * 40,
        server_version="b" * 40,
        images=("ais0c-platform:1.2.3-test", "example.com/image:1@sha256:" + "c" * 64),
    )
    image_ids = {image: f"sha256:{index:064x}" for index, image in enumerate(plan.images, 1)}
    sums = {"ais0c-images-1.2.3-test.tar.gz": "d" * 64, "ais0c-files-1.2.3-test.tar.gz": "e" * 64}

    rendered = render_release_md(plan, image_ids, sums)

    assert rendered.startswith("# ais0c 1.2.3-test\n")
    assert plan.commit in rendered
    assert plan.server_version in rendered
    for image, image_id in image_ids.items():
        assert f"| `{image}` | `{image_id}` |" in rendered
    for name, digest in sums.items():
        assert f"| `{name}` | `{digest}` |" in rendered


@pytest.mark.skipif(
    os.environ.get("AIS0C_RELEASE_TEST") != "1",
    reason="set AIS0C_RELEASE_TEST=1 to build the real release",
)
def test_a_real_release_builds_and_verifies(tmp_path: Path) -> None:
    fork_value = os.environ.get("AIS0C_RELEASE_FORK")
    if not fork_value:
        pytest.skip("set AIS0C_RELEASE_FORK to the qradar-mcp fork")
    version = "0.0.0-test"
    out = tmp_path / "release"
    tags = [f"{name}:{version}" for name in OWN_IMAGES]
    try:
        assert main(["--version", version, "--fork", fork_value, "--out", str(out)]) == 0
        subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=out, check=True)
        files_done = subprocess.run(
            ["tar", "-tzf", f"ais0c-files-{version}.tar.gz"],
            cwd=out,
            capture_output=True,
            check=True,
            text=True,
        )
        archived = set(files_done.stdout.splitlines())
        for path in ARCHIVED:
            assert any(name.startswith(f"ais0c-{version}/{path}") for name in archived)
        images_done = subprocess.run(
            ["tar", "-tzf", f"ais0c-images-{version}.tar.gz"],
            cwd=out,
            capture_output=True,
            check=True,
            text=True,
        )
        assert "manifest.json" in images_done.stdout.splitlines()
        with tarfile.open(out / f"ais0c-images-{version}.tar.gz", "r:gz") as images_archive:
            index_file = images_archive.extractfile("index.json")
            assert index_file is not None
            index = json.load(index_file)
        manifests = index["manifests"]
        assert all("io.containerd.image.name" in manifest["annotations"] for manifest in manifests)
        saved_names = {
            _normalized_saved_name(manifest["annotations"]["io.containerd.image.name"])
            for manifest in manifests
        }
        plan = build_release.plan_release(ROOT, version)
        expected_names = {_normalized_saved_name(save_reference(image)) for image in plan.images}
        assert expected_names <= saved_names
    finally:
        subprocess.run(["docker", "image", "rm", *tags], check=False)


def _normalized_saved_name(reference: str) -> str:
    if reference.startswith("docker.io/"):
        return reference
    if "/" not in reference:
        return f"docker.io/library/{reference}"
    return reference
