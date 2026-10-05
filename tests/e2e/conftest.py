"""Fixtures of the end-to-end lab test (tests/e2e/README.md).

This directory is not a package (see packages/storage/tests/conftest.py). The test imports
`e2e_support` from here and reads Triage run histories with `worker_support` from the case
worker's tests, so both directories go on sys.path.
"""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "services" / "worker" / "tests")]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
