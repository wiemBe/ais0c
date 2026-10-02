"""Acceptance criterion 11: the agents package's import and provider-name boundaries.

import-linter and the CI provider-name check enforce these on every PR; these tests make the
same rules fail fast under pytest. Provider names are read from the CI workflow, because this
file is itself scanned by that check.
"""

import ast
import re
from pathlib import Path

import yaml

from .helpers import REPO_ROOT

PACKAGE = REPO_ROOT / "packages/agents"
SOURCE = PACKAGE / "src/ais0c_agents"
# The LiteLLM client and the dependency list that names its extra (AGENTS.md hard rule 3).
PROVIDER_NAME_EXEMPT = {"src/ais0c_agents/llm.py", "pyproject.toml"}
# MCP SDKs and QRadar or Falcon clients (AGENTS.md hard rule 1).
SECURITY_PRODUCT_MODULES = ("mcp", "fastmcp", "qradar", "falcon", "crowdstrike")


def provider_names() -> list[str]:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
    [step] = [step for step in steps if step.get("id") == "provider-names"]
    return step["env"]["PROVIDER_NAMES"].split("|")


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_agents_import_no_mcp_sdk_and_no_security_product_client() -> None:
    for path in SOURCE.rglob("*.py"):
        for module in imported_modules(path):
            assert not module.lower().startswith(SECURITY_PRODUCT_MODULES), (path.name, module)


def test_only_llm_imports_the_model_client() -> None:
    names = provider_names()

    importers = {
        path.name
        for path in SOURCE.rglob("*.py")
        for module in imported_modules(path)
        if any(name in module.lower() for name in names)
    }

    assert importers == {"llm.py"}


def test_no_provider_name_outside_the_exempt_files() -> None:
    pattern = re.compile("|".join(provider_names()), flags=re.IGNORECASE)

    for path in PACKAGE.rglob("*"):
        relative = path.relative_to(PACKAGE).as_posix()
        if path.is_dir() or "__pycache__" in path.parts or relative in PROVIDER_NAME_EXEMPT:
            continue
        assert not pattern.search(relative), relative
        assert not pattern.search(path.read_text(encoding="utf-8")), relative
