"""Guards for the repository-wide checks set up in T-001.

They fail when the import contracts drift from the dependency table in
docs/impl/repo-structure.md, when contracts or policy lose strict type checking, when CI stops
running a required check, when the provider name check stops catching provider names, or when
local and secret files stop being ignored.
"""

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
LLM_CLIENT = "packages/agents/src/ais0c_agents/llm.py"
# Declares the dependency that brings the client llm.py imports.
LLM_CLIENT_DEPENDENCIES = "packages/agents/pyproject.toml"


def load_pyproject() -> dict[str, Any]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)


def load_ci_workflow() -> dict[Any, Any]:
    return yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def ci_steps() -> dict[str, dict[str, Any]]:
    """Return the CI steps that have an id, keyed by id."""
    jobs = load_ci_workflow()["jobs"]
    return {step["id"]: step for job in jobs.values() for step in job["steps"] if "id" in step}


def executable(name: str) -> str:
    path = shutil.which(name)
    assert path is not None, f"{name} is not installed"
    return path


def import_name(package: str) -> str:
    """Map a package in the docs table to its import name: services/api -> ais0c_api."""
    return "ais0c_" + package.rsplit("/", 1)[-1].replace("-", "_")


def dependency_table() -> dict[str, set[str]]:
    """Parse the table under "Bağımlılık kuralları" in docs/impl/repo-structure.md."""
    text = (REPO_ROOT / "docs/impl/repo-structure.md").read_text(encoding="utf-8")
    section = text.split("## Bağımlılık kuralları", 1)[1].split("\n## ", 1)[0]
    table: dict[str, set[str]] = {}
    for line in section.splitlines():
        row = re.fullmatch(r"\| `([^`]+)` \| (.+) \|", line.strip())
        if row:
            package, allowed = row.groups()
            table[import_name(package)] = {
                import_name(n) for n in re.findall(r"`([^`]+)`", allowed)
            }
    return table


# --- import boundaries --------------------------------------------------------------------


def test_import_contracts_match_the_dependency_table() -> None:
    config = load_pyproject()["tool"]["importlinter"]
    root_packages = set(config["root_packages"])
    # Rows for packages that are not Python packages yet (services/mcp-gateway arrives in
    # T-011, apps/ui is TypeScript) cannot be enforced.
    table = {pkg: allowed for pkg, allowed in dependency_table().items() if pkg in root_packages}
    # harness is not in the table: it may import any package, and no package may import it.
    assert root_packages - table.keys() == {"ais0c_harness"}

    internal = [
        contract
        for contract in config["contracts"]
        if contract["type"] == "forbidden" and set(contract["forbidden_modules"]) <= root_packages
    ]
    by_source = {contract["source_modules"][0]: contract for contract in internal}
    assert len(by_source) == len(internal), "one contract per package"
    assert by_source.keys() == table.keys()
    for package, allowed in table.items():
        contract = by_source[package]
        assert contract["source_modules"] == [package]
        assert set(contract["forbidden_modules"]) == root_packages - allowed - {package}, package
        assert contract["allow_indirect_imports"] is True


def test_agents_cannot_import_mcp_sdks_or_security_product_clients() -> None:
    config = load_pyproject()["tool"]["importlinter"]
    banned = {"mcp", "fastmcp", "qradar_mcp", "falcon_mcp", "falconpy"}
    assert config["include_external_packages"] is True
    assert any(
        contract["type"] == "forbidden"
        and contract["source_modules"] == ["ais0c_agents"]
        and banned <= set(contract["forbidden_modules"])
        and not contract.get("allow_indirect_imports", False)
        for contract in config["contracts"]
    )


# --- type checking ------------------------------------------------------------------------


@pytest.mark.parametrize("package", ["packages/contracts", "packages/policy"])
def test_pyright_is_strict_for(package: str) -> None:
    assert (REPO_ROOT / package / "src").is_dir()
    assert package in load_pyproject()["tool"]["pyright"]["strict"]


# --- CI -----------------------------------------------------------------------------------


def test_ci_runs_on_every_pull_request() -> None:
    workflow = load_ci_workflow()
    triggers = workflow["on" if "on" in workflow else True]  # YAML 1.1 reads `on` as true
    assert "pull_request" in triggers
    assert triggers["pull_request"] is None  # no branch or path filters


@pytest.mark.parametrize(
    ("step_id", "command"),
    [
        ("ruff-check", "uv run ruff check"),
        ("ruff-format", "uv run ruff format --check"),
        ("pyright", "uv run pyright"),
        ("pytest", "uv run pytest"),
        ("import-linter", "uv run lint-imports"),
        ("gitleaks", 'gitleaks" git --redact'),
        ("provider-names", "git grep"),
    ],
)
def test_ci_runs_required_check(step_id: str, command: str) -> None:
    assert command in ci_steps()[step_id]["run"]


# --- provider name check ------------------------------------------------------------------
# These tests run the CI step's script in a scratch git repository. Provider names are read
# from the workflow, because this file is itself scanned by the check.


def provider_names() -> list[str]:
    return ci_steps()["provider-names"]["env"]["PROVIDER_NAMES"].split("|")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    subprocess.run([executable("git"), "init", "--quiet", str(tmp_path)], check=True)  # noqa: S603
    return tmp_path


def run_provider_check(repo: Path) -> subprocess.CompletedProcess[str]:
    """Run the step's script the way GitHub Actions runs a `shell: bash` step."""
    assert load_ci_workflow()["defaults"]["run"]["shell"] == "bash"
    step = ci_steps()["provider-names"]
    # GIT_* variables (set inside git hooks, for example) would point git at another repository.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | step["env"]
    return subprocess.run(  # noqa: S603
        [executable("bash"), "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_provider_check_passes_without_provider_names(scratch_repo: Path) -> None:
    write(scratch_repo / "packages/agents/src/ais0c_agents/triage.py", 'MODEL = "soc-reasoning"\n')

    result = run_provider_check(scratch_repo)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "directory", ["packages", "services", "apps", "prompts", "harness", "skills"]
)
def test_provider_check_rejects_every_provider_name(scratch_repo: Path, directory: str) -> None:
    names = provider_names()
    for index, name in enumerate(names):
        write(scratch_repo / directory / f"module_{index}.py", f'MODEL = "{name.upper()}/model"\n')

    result = run_provider_check(scratch_repo)

    assert result.returncode == 1
    for index in range(len(names)):
        assert f"{directory}/module_{index}.py:1:" in result.stdout


def test_provider_check_rejects_provider_names_in_paths(scratch_repo: Path) -> None:
    name = provider_names()[0]
    write(scratch_repo / "prompts" / name / "v1.md", "Neutral prompt text.\n")

    result = run_provider_check(scratch_repo)

    assert result.returncode == 1
    assert f"prompts/{name}/v1.md" in result.stdout


def test_provider_check_exempts_only_the_litellm_client_and_its_dependency(
    scratch_repo: Path,
) -> None:
    name = provider_names()[0]
    write(scratch_repo / LLM_CLIENT, f"from {name} import AsyncClient\n")
    write(scratch_repo / LLM_CLIENT_DEPENDENCIES, f'dependencies = ["pydantic-ai-slim[{name}]"]\n')
    assert run_provider_check(scratch_repo).returncode == 0

    write(scratch_repo / "packages/agents/src/ais0c_agents/client.py", f"import {name}\n")
    write(scratch_repo / "packages/activities/pyproject.toml", f'dependencies = ["{name}"]\n')
    result = run_provider_check(scratch_repo)

    assert result.returncode == 1
    assert "ais0c_agents/client.py" in result.stdout
    assert "packages/activities/pyproject.toml" in result.stdout
    assert "llm.py" not in result.stdout
    assert LLM_CLIENT_DEPENDENCIES not in result.stdout


def test_provider_check_does_not_scan_model_config_or_docs(scratch_repo: Path) -> None:
    name = provider_names()[0]
    for path in ("config/litellm/litellm.dev.yaml", "config/models/registry.dev.yaml", "docs/x.md"):
        write(scratch_repo / path, f"model: {name}/model\n")

    assert run_provider_check(scratch_repo).returncode == 0


# --- ignored files ------------------------------------------------------------------------


def is_ignored(path: str) -> bool:
    result = subprocess.run(  # noqa: S603
        [executable("git"), "check-ignore", "--quiet", "--no-index", path],
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode in (0, 1), f"git check-ignore failed for {path}"
    return result.returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "deploy/compose/.env",
        ".venv/bin/python",
        "packages/contracts/src/ais0c_contracts/__pycache__/x.cpython-312.pyc",
        ".pytest_cache/v/cache/nodeids",
        "harness/x.pyc",
    ],
)
def test_gitignore_excludes_local_and_secret_files(path: str) -> None:
    assert is_ignored(path)


def test_gitignore_keeps_env_examples() -> None:
    assert not is_ignored("deploy/compose/.env.example")
