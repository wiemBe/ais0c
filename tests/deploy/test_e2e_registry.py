"""The end-to-end tests read their model registry from the environment (T-074, T-104).

Needs no model and no lab.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
E2E_DIR = REPO_ROOT / "tests/e2e"


def registry_seen_by_e2e_support(env: dict[str, str]) -> str:
    result = subprocess.run(
        [sys.executable, "-c", "import e2e_support; print(e2e_support.MODEL_REGISTRY)"],
        cwd=E2E_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def clean_env(**extra: str) -> dict[str, str]:
    import os

    env = {k: v for k, v in os.environ.items() if k != "AIS0C_E2E_MODEL_REGISTRY"}
    return env | extra


def test_e2e_registry_comes_from_the_environment() -> None:
    assert registry_seen_by_e2e_support(clean_env()) == "config/models/registry.dev.yaml"
    free = "config/models/registry.dev-free.yaml"
    assert registry_seen_by_e2e_support(clean_env(AIS0C_E2E_MODEL_REGISTRY=free)) == free
    assert (REPO_ROOT / free).exists()
