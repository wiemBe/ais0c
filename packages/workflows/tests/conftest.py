"""Temporal test environment for the workflow tests.

Every test gets its own time-skipping test server, so workflow IDs never clash between tests.
Workflows run in Temporal's sandbox (the worker's default runner) with fake activities
registered under the real activity names.

This directory is not a package: with `--import-mode=importlib` it would clash with the `tests`
package of another workspace member. The test modules import the helper module here
(`workflow_fakes`) by name, so the directory goes on sys.path.
"""

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def env() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        yield env
