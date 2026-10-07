from collections.abc import Iterator

import pytest
from pydantic_ai import models


@pytest.fixture(autouse=True)
def _no_real_model_requests() -> Iterator[None]:
    """Harness tests never reach a real model (AGENTS.md); TestModel and FunctionModel still run."""
    with models.override_allow_model_requests(False):
        yield
